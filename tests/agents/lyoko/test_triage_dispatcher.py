"""Unit tests for the modular deterministic triage dispatcher."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from lyoko.application.incidents.triage.base import TriageResult
from lyoko.application.incidents.use_cases.triage import TriageIncidentUseCase


def create_triage_node(handlers=None):
    return TriageIncidentUseCase(handlers=handlers).execute


@pytest.mark.asyncio
async def test_triage_dispatcher_falls_through_when_no_handler_matches():
    node = create_triage_node(handlers=[])
    state = {"event_type": "alert", "alert_name": "UnknownAlert"}
    result = await node(state, {})
    assert result == {"triage": "diagnose"}


@pytest.mark.asyncio
async def test_triage_dispatcher_executes_first_matching_handler():
    handler = MagicMock()
    handler.can_handle = MagicMock(return_value=True)
    handler.execute = AsyncMock(
        return_value=TriageResult(
            handled=True,
            root_cause="Found issue",
            plan="Fixed issue",
            action_taken="Took action",
            is_resolved=True,
        )
    )
    node = create_triage_node(handlers=[handler])
    state = {"event_type": "alert", "alert_name": "TestAlert"}
    result = await node(state, {})
    assert result["triage"] == "handled"
    assert result["root_cause"] == "Found issue"
    assert result["is_resolved"] is True


@pytest.mark.asyncio
async def test_triage_dispatcher_falls_through_if_handler_returns_unhandled():
    handler = MagicMock()
    handler.can_handle = MagicMock(return_value=True)
    handler.execute = AsyncMock(return_value=TriageResult(handled=False))
    node = create_triage_node(handlers=[handler])
    state = {"event_type": "alert", "alert_name": "TestAlert"}
    result = await node(state, {})
    assert result == {"triage": "diagnose"}


@pytest.mark.asyncio
async def test_triage_dispatcher_falls_through_if_handler_raises_exception():
    handler = MagicMock()
    handler.can_handle = MagicMock(return_value=True)
    handler.execute = AsyncMock(side_effect=RuntimeError("Transient error"))
    node = create_triage_node(handlers=[handler])
    state = {"event_type": "alert", "alert_name": "TestAlert"}
    result = await node(state, {})
    assert result == {"triage": "diagnose"}
