"""Unit tests for workflow domain specialist nodes."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from lyoko.application.agents.task_runner import ExecuteSpecialistTaskUseCase


def create_specialist_node(**kwargs):
    return ExecuteSpecialistTaskUseCase(**kwargs).execute


@pytest.mark.asyncio
async def test_specialist_node_runs_specialist_and_updates_state():
    mock_specialist = MagicMock()
    mock_specialist.name = "k8s_agent"
    mock_specialist.domain = "kubernetes"
    mock_specialist.run = AsyncMock(return_value="Pods are healthy.")

    node = create_specialist_node(
        domain="kubernetes",
        specialist=mock_specialist,
    )

    state = {
        "event_id": "test-123",
        "pending_delegation": {
            "domain": "kubernetes",
            "task": "Check pods in default namespace",
        },
        "delegation_history": [],
    }

    result = await node(state, {})

    from lyoko.domain.models.incident import IncidentStatus

    assert result["pending_delegation"] is None
    assert result["status"] == IncidentStatus.DELEGATED
    assert result["delegated_to"] == "kubernetes"
    assert len(result["delegation_history"]) == 1
    assert result["delegation_history"][0]["domain"] == "kubernetes"
    assert result["delegation_history"][0]["task"] == "Check pods in default namespace"
    assert result["delegation_history"][0]["response"] == "Pods are healthy."
    mock_specialist.run.assert_awaited_once()


@pytest.mark.asyncio
async def test_specialist_node_updates_progress_message():
    mock_specialist = MagicMock()
    mock_specialist.run = AsyncMock(return_value="Restarted 0 pods.")
    mock_chat = AsyncMock()

    node = create_specialist_node(
        domain="kubernetes",
        specialist=mock_specialist,
        chat_manager=mock_chat,
    )

    state = {
        "event_id": "test-123",
        "pending_delegation": {
            "domain": "kubernetes",
            "task": "Investigate bentopdf",
        },
        "progress_message_id": "999",
        "progress_chat_id": "12345",
    }

    result = await node(state, {})
    assert result["status"] == "delegated"
    assert mock_chat.edit_message.await_count == 2


@pytest.mark.asyncio
async def test_specialist_node_handles_missing_pending_delegation():
    mock_specialist = MagicMock()
    mock_specialist.domain = "unifi"

    node = create_specialist_node(
        domain="unifi",
        specialist=mock_specialist,
    )

    state = {
        "event_id": "test-123",
        "pending_delegation": None,
        "delegation_history": [],
    }

    result = await node(state, {})
    assert result["pending_delegation"] is None
