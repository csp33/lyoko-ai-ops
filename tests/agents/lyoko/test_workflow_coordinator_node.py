"""Unit tests for workflow coordinator node."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from lyoko.application.workflow.coordinator import CoordinateWorkflowUseCase
from lyoko.application.workflow.routing.edges import WorkflowRouteSelector

choose_coordinator_next = WorkflowRouteSelector.choose_coordinator_next


def create_coordinator_node(**kwargs):
    return CoordinateWorkflowUseCase(**kwargs).execute


@pytest.mark.asyncio
async def test_coordinator_handles_message():
    mock_llm = MagicMock()
    mock_llm.chat = AsyncMock(return_value="Everything looks operational.")

    mock_mcp = MagicMock()
    mock_mcp.get_langchain_tools.return_value = []

    node = create_coordinator_node(
        mcp_client=mock_mcp,
        llm=mock_llm,
    )
    state = {
        "event_type": "message",
        "text": "How is the homelab?",
    }

    result = await node(state, {})
    assert result["reply"] == "Everything looks operational."


@pytest.mark.asyncio
async def test_coordinator_handles_alert_diagnose():
    mock_runner = AsyncMock(return_value="ROOT_CAUSE: Pod OOM\nACTIONABLE: yes\nPLAN: Scale up")
    from lyoko.application.incidents.use_cases.diagnose import DiagnoseIncidentUseCase

    diag_uc = DiagnoseIncidentUseCase(
        mcp_client=None,
        llm=MagicMock(),
        runner=mock_runner,
    )
    node = create_coordinator_node(
        mcp_client=None,
        llm=MagicMock(),
        diagnose_use_case=diag_uc,
    )
    state = {
        "event_type": "alert",
        "alert_name": "KubePodCrashLooping",
        "labels": {"alertname": "KubePodCrashLooping"},
        "annotations": {},
    }

    result = await node(state, {})
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

    # If alert routes to remediate
    state_alert = {
        "event_type": "alert",
        "requires_escalation": True,
    }
    assert choose_coordinator_next(state_alert) == CoordinatorNext.REMEDIATE

    # If autonomous single-pass coordinator set NOTIFY
    state_notify = {
        "event_type": "alert",
        "coordinator_next": CoordinatorNext.NOTIFY,
    }
    assert choose_coordinator_next(state_notify) == CoordinatorNext.NOTIFY
