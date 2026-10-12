"""Unit tests for OrphanRecoveryService."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from lyoko.application.incidents.recovery import OrphanRecoveryService


@pytest.mark.asyncio
async def test_recovery_noop_without_checkpointer():
    """Recovery returns empty list when no checkpointer is provided."""
    service = OrphanRecoveryService(workflow_engine=MagicMock(), checkpointer=None)
    recovered = await service.recover_orphaned_incidents()
    assert recovered == []


@pytest.mark.asyncio
async def test_recovery_skips_non_incident_threads():
    """Threads that are not incidents (e.g. chat sessions) are ignored."""
    mock_tuple = MagicMock()
    mock_tuple.config = {"configurable": {"thread_id": "chat-session-001"}}

    async def mock_alist(config):
        yield mock_tuple

    mock_checkpointer = MagicMock()
    mock_checkpointer.alist = mock_alist

    mock_engine = MagicMock()

    service = OrphanRecoveryService(workflow_engine=mock_engine, checkpointer=mock_checkpointer)
    recovered = await service.recover_orphaned_incidents()

    assert recovered == []
    mock_engine.aget_state.assert_not_called()


@pytest.mark.asyncio
async def test_recovery_skips_completed_incidents():
    """Incidents with no remaining steps (snapshot.next is empty) are not resumed."""
    mock_tuple = MagicMock()
    mock_tuple.config = {"configurable": {"thread_id": "incident-pod-crash-001"}}

    async def mock_alist(config):
        yield mock_tuple

    mock_checkpointer = MagicMock()
    mock_checkpointer.alist = mock_alist

    mock_snapshot = MagicMock()
    mock_snapshot.next = ()  # Completed

    mock_engine = MagicMock()
    mock_engine.aget_state = AsyncMock(return_value=mock_snapshot)
    mock_engine.ainvoke = AsyncMock()

    service = OrphanRecoveryService(workflow_engine=mock_engine, checkpointer=mock_checkpointer)
    recovered = await service.recover_orphaned_incidents()

    assert recovered == []
    mock_engine.aget_state.assert_called_once()
    mock_engine.ainvoke.assert_not_called()


@pytest.mark.asyncio
async def test_recovery_resumes_interrupted_incident():
    """Incidents with remaining steps (snapshot.next is non-empty) are resumed."""
    mock_tuple = MagicMock()
    mock_tuple.config = {"configurable": {"thread_id": "incident-pod-crash-002"}}

    async def mock_alist(config):
        yield mock_tuple

    mock_checkpointer = MagicMock()
    mock_checkpointer.alist = mock_alist

    mock_snapshot = MagicMock()
    mock_snapshot.next = ("remediate",)  # Pending step

    mock_engine = MagicMock()
    mock_engine.aget_state = AsyncMock(return_value=mock_snapshot)
    mock_engine.ainvoke = AsyncMock()

    service = OrphanRecoveryService(workflow_engine=mock_engine, checkpointer=mock_checkpointer)
    recovered = await service.recover_orphaned_incidents()

    assert recovered == ["incident-pod-crash-002"]
    mock_engine.aget_state.assert_called_once()
    # Await brief moment for asyncio.create_task to run
    import asyncio

    await asyncio.sleep(0.01)
    mock_engine.ainvoke.assert_called_once_with(None, config=mock_tuple.config)


@pytest.mark.asyncio
async def test_recovery_drains_alist_before_calling_aget_state():
    """Verify checkpointer iterator is fully drained before aget_state is called to prevent lock deadlock."""
    generator_active = True
    deadlock_detected = False

    async def mock_alist(config):
        nonlocal generator_active
        mock_t = MagicMock()
        mock_t.config = {"configurable": {"thread_id": "incident-deadlock-test"}}
        yield mock_t
        generator_active = False

    async def mock_aget_state(cfg):
        nonlocal deadlock_detected
        if generator_active:
            deadlock_detected = True
        snap = MagicMock()
        snap.next = ()
        return snap

    mock_checkpointer = MagicMock()
    mock_checkpointer.alist = mock_alist
    mock_engine = MagicMock()
    mock_engine.aget_state = AsyncMock(side_effect=mock_aget_state)

    service = OrphanRecoveryService(workflow_engine=mock_engine, checkpointer=mock_checkpointer)
    await service.recover_orphaned_incidents()

    assert not deadlock_detected
    assert not generator_active
