"""Unit tests for workflow coordinator node."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from lyoko.application.workflow.coordinator import CoordinateWorkflowUseCase
from lyoko.application.workflow.routing.edges import WorkflowRouteSelector

choose_coordinator_next = WorkflowRouteSelector.choose_coordinator_next


@pytest.mark.asyncio
async def test_coordinator_handles_message():
    mock_chat_uc = MagicMock()
    mock_chat_uc.execute = AsyncMock(return_value={"reply": "Everything looks operational."})
    mock_incident_uc = MagicMock()

    coordinator = CoordinateWorkflowUseCase(
        chat_use_case=mock_chat_uc,
        incident_use_case=mock_incident_uc,
    )
    state = {
        "event_type": "message",
        "text": "How is the homelab?",
    }

    result = await coordinator.execute(state, {})
    assert result["reply"] == "Everything looks operational."


@pytest.mark.asyncio
async def test_coordinator_handles_alert_incident():
    mock_chat_uc = MagicMock()
    mock_incident_uc = MagicMock()
    mock_incident_uc.execute = AsyncMock(
        return_value={
            "root_cause": "Pod OOM",
            "action_taken": "Scaled memory",
            "verification": "Pod running",
            "requires_escalation": False,
            "status": "RESOLVED",
        }
    )
    coordinator = CoordinateWorkflowUseCase(
        chat_use_case=mock_chat_uc,
        incident_use_case=mock_incident_uc,
    )
    state = {
        "event_type": "alert",
        "alert_name": "KubePodCrashLooping",
        "labels": {"alertname": "KubePodCrashLooping"},
        "annotations": {},
    }

    result = await coordinator.execute(state, {})
    assert "Pod OOM" in result["root_cause"]
    assert result["requires_escalation"] is False


def test_choose_coordinator_next():
    from lyoko.domain.models.incident import CoordinatorNext

    # If message and finished
    state_chat_done = {
        "event_type": "message",
        "route": "chat",
        "reply": "All good",
    }
    assert choose_coordinator_next(state_chat_done) == CoordinatorNext.CHAT_END

    # If alert
    state_alert = {
        "event_type": "alert",
    }
    assert choose_coordinator_next(state_alert) == CoordinatorNext.NOTIFY

    # If autonomous single-pass coordinator set NOTIFY
    state_notify = {
        "event_type": "alert",
        "coordinator_next": CoordinatorNext.NOTIFY,
    }
    assert choose_coordinator_next(state_notify) == CoordinatorNext.NOTIFY
