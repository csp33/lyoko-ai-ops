"""Unit tests for isolated LYOKO workflow nodes and helper functions."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from lyoko.application.chat.use_cases.handle_turn import HandleChatTurnUseCase
from lyoko.application.context.formatter import IncidentContextFormatter
from lyoko.application.incidents.use_cases.diagnose import DiagnoseIncidentUseCase
from lyoko.application.incidents.use_cases.notify import format_incident_report
from lyoko.application.workflow.routing.edges import WorkflowRouteSelector
from lyoko.application.workflow.routing.recovery import (
    RECOVERY_ACKNOWLEDGEMENT,
    RecoveryNotificationDetector,
)
from lyoko.application.workflow.routing.route_event import RouteEventUseCase
from lyoko.domain.interfaces.llm import LLMClientInterface
from lyoko.domain.models.state import is_message

format_pairs = IncidentContextFormatter.format_pairs
incident_context = IncidentContextFormatter.format_context
origin = IncidentContextFormatter.format_origin
truncate = IncidentContextFormatter.truncate
choose_branch = WorkflowRouteSelector.choose_branch
is_recovery_notification = RecoveryNotificationDetector.is_recovery_notification


def create_chat_node(**kwargs):
    return HandleChatTurnUseCase(**kwargs).execute


def create_route_node(**kwargs):
    return RouteEventUseCase(**kwargs).execute


def test_workflow_node_helpers():
    pairs = {"a": "1", "b": "2"}
    formatted = format_pairs(pairs)
    assert "- a: 1" in formatted
    assert "- b: 2" in formatted

    empty_formatted = format_pairs({})
    assert empty_formatted == "- (none)"

    msg_state = {"event_type": "message", "text": "restart router"}
    assert is_message(msg_state) is True
    assert "Problem reported" in incident_context(msg_state)
    assert "Chat request: restart router" in origin(msg_state)

    alert_state = {"event_type": "alert", "alert_name": "HighMemory"}
    assert is_message(alert_state) is False
    assert "Alert: HighMemory" in incident_context(alert_state)
    assert "Alert: HighMemory" in origin(alert_state)

    long_text = "word " * 100
    assert len(truncate(long_text)) <= 205


def test_format_incident_report():
    state = {
        "is_resolved": True,
        "alert_name": "CrashLoopBackOff",
        "labels": {"namespace": "media", "pod": "sonarr-0"},
        "root_cause": "Disk full",
        "action_taken": "Cleaned up temp logs",
        "actions": [{"tool": "cleanup_disk", "outcome": "auto_approved"}],
        "verification": "Pod running 0 restarts",
    }
    report = format_incident_report(state)
    assert "**LYOKO Incident Report**" in report
    assert "**Status:** ✅ RESOLVED" in report
    assert "CrashLoopBackOff" in report
    assert "Disk full" in report
    assert "Cleaned up temp logs" in report

    message_state = {
        "event_type": "message",
        "is_resolved": True,
        "text": "autosync arr-stack please",
        "root_cause": "Autosync disabled",
        "action_taken": "Enabled autosync",
    }
    message_report = format_incident_report(message_state)
    assert "**Report:**" not in message_report
    assert "autosync arr-stack please" not in message_report


@pytest.mark.asyncio
async def test_route_node_and_choose_branch():
    assert choose_branch({"route": "incident"}) == "diagnose"
    assert choose_branch({"route": "chat"}) == "chat"

    # Alert always routes to incident without calling LLM
    route_node_no_llm = create_route_node(llm=None)
    res_alert = await route_node_no_llm({"event_type": "alert"}, MagicMock())
    assert res_alert["route"] == "incident"

    # Message with no LLM defaults to chat
    res_msg = await route_node_no_llm({"event_type": "message", "text": "hello"}, MagicMock())
    assert res_msg["route"] == "chat"

    # Message with LLM
    mock_llm = MagicMock(spec=LLMClientInterface)
    mock_llm.chat = AsyncMock(return_value="INCIDENT")
    route_node_llm = create_route_node(llm=mock_llm)
    res_routed = await route_node_llm({"event_type": "message", "text": "pod crashed"}, MagicMock())
    assert res_routed["route"] == "incident"


def test_status_callback_reads_only_config_configurable():
    from lyoko.application.agents.supervisor import status_callback

    sentinel = object()
    assert status_callback({"configurable": {"on_status": sentinel}}) is sentinel
    assert status_callback({"configurable": {}}) is None
    assert status_callback(None) is None
    assert status_callback("not-a-config") is None


@pytest.mark.asyncio
async def test_diagnose_node_forwards_streaming_status_callback():
    """The incident branch must forward on_status so the placeholder shows steps, not 'Thinking…'."""
    captured: dict[str, object] = {}

    async def fake_run_supervised(*args, **kwargs):  # noqa: ANN002, ANN003
        captured.update(kwargs)
        return "ROOT_CAUSE: drift\nACTIONABLE: no\nPLAN: check"

    node = DiagnoseIncidentUseCase(
        mcp_client=object(), llm=object(), runner=fake_run_supervised
    ).execute
    sentinel = object()
    state = {"event_type": "message", "text": "hi", "event_id": "evt-1", "session_id": "s1"}
    await node(state, {"configurable": {"on_status": sentinel}})

    assert captured.get("on_status") is sentinel


def test_is_recovery_notification():
    gatus = (
        "⛑ Gatus\nAn alert for BentoPDF/pdf.internal.cspaez.org — HTTP has been resolved:\n"
        "—\n    healthcheck passing successfully 5 time(s) in a row"
    )
    assert is_recovery_notification(gatus) is True
    assert is_recovery_notification("The alert has been cleared.") is True
    assert is_recovery_notification("[RESOLVED] Service back up") is True
    assert is_recovery_notification("status: resolved") is True
    assert is_recovery_notification("This is unresolved and still broken") is False
    assert is_recovery_notification("radarr keeps crashing, fix it") is False
    assert is_recovery_notification("") is False


@pytest.mark.asyncio
async def test_route_node_sends_recovery_to_chat_without_llm():
    mock_llm = MagicMock(spec=LLMClientInterface)
    mock_llm.chat = AsyncMock(side_effect=AssertionError("LLM must not be called for recoveries"))
    node = create_route_node(llm=mock_llm)

    res = await node({"event_type": "message", "text": "HTTP has been resolved"}, MagicMock())

    assert res["route"] == "chat"
    mock_llm.chat.assert_not_called()


@pytest.mark.asyncio
async def test_chat_node_acknowledges_recovery_without_supervisor():
    supervisor = MagicMock()
    supervisor.coordinate = AsyncMock()
    node = create_chat_node(mcp_client=object(), llm=object(), supervisor=supervisor)

    res = await node(
        {"event_type": "message", "text": "An alert has been resolved: passing successfully"},
        MagicMock(),
    )

    assert res["reply"] == RECOVERY_ACKNOWLEDGEMENT
    supervisor.coordinate.assert_not_called()
