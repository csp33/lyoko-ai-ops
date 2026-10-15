"""Unit tests for newly introduced application UseCase classes."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from lyoko.application.incidents.use_cases.notify import (
    NotifyIncidentReportUseCase,
)
from lyoko.application.workflow.routing.route_event import RouteEventUseCase
from lyoko.domain.interfaces.llm import LLMClientInterface
from sector5_mcp.application.use_cases.discover_tools import DiscoverToolsUseCase
from sector5_mcp.application.use_cases.execute_tool import ExecuteToolUseCase
from sector5_mcp.domain.models.upstream import ToolDefinition, ToolResult


@pytest.mark.asyncio
async def test_route_event_use_case():
    use_case_no_llm = RouteEventUseCase(llm=None)
    assert (await use_case_no_llm.execute({"event_type": "alert"})) == {"route": "incident"}
    assert (await use_case_no_llm.execute({"event_type": "message", "text": "hello"})) == {
        "route": "chat"
    }

    mock_llm = MagicMock(spec=LLMClientInterface)
    mock_llm.chat = AsyncMock(return_value="INCIDENT")
    use_case_llm = RouteEventUseCase(llm=mock_llm)
    assert (await use_case_llm.execute({"event_type": "message", "text": "pod down"})) == {
        "route": "incident"
    }


@pytest.mark.asyncio
async def test_notify_incident_report_use_case():
    mock_chat_manager = AsyncMock()
    use_case = NotifyIncidentReportUseCase(chat_manager=mock_chat_manager)

    state = {
        "event_type": "message",
        "text": "test issue",
        "root_cause": "cpu throttle",
        "action_taken": "unthrottled",
    }
    result = await use_case.execute(state)
    assert "reply" in result
    assert "cpu throttle" in result["reply"]


@pytest.mark.asyncio
async def test_sector5_mcp_use_cases():
    mock_registry = AsyncMock()
    tool_def = ToolDefinition(name="k8s.get_pods", description="List pods")
    mock_registry.discover_tools.return_value = [tool_def]
    mock_registry.get_domain_tools.return_value = [tool_def]

    discover_uc = DiscoverToolsUseCase(registry=mock_registry)
    all_tools = await discover_uc.execute_all()
    assert len(all_tools) == 1
    domain_tools = await discover_uc.execute_for_domain("k8s")
    assert len(domain_tools) == 1
    single_def = await discover_uc.execute_get_definition("k8s.get_pods")
    assert single_def is not None

    mock_guardrail = MagicMock()
    mock_client = AsyncMock()
    mock_client.call_tool.return_value = ToolResult(content=[{"text": "ok"}], status="success")
    mock_registry.resolve_tool_routing = MagicMock(return_value=("k8s", mock_client))

    exec_uc = ExecuteToolUseCase(registry=mock_registry, guardrail=mock_guardrail)
    res = await exec_uc.execute("k8s.get_pods", {"namespace": "default"})
    assert res.content[0]["text"] == "ok"
    assert mock_guardrail.validate_tool_call.called


@pytest.mark.asyncio
async def test_triage_incident_use_case():
    from lyoko.application.incidents.use_cases.triage import (
        TRIAGE_DIAGNOSE,
        TriageIncidentUseCase,
    )

    # When no matching handler
    uc = TriageIncidentUseCase(handlers=[])
    res = await uc.execute({"event_type": "alert"}, {})
    assert res == {"triage": TRIAGE_DIAGNOSE}

    handler = MagicMock()
    handler.can_handle.return_value = False
    uc_with_handlers = TriageIncidentUseCase(handlers=[handler])
    # Message does not match any deterministic handler
    res2 = await uc_with_handlers.execute({"event_type": "message", "text": "normal message"}, {})
    assert res2 == {"triage": TRIAGE_DIAGNOSE}


@pytest.mark.asyncio
async def test_coordinate_workflow_use_case():
    from lyoko.application.workflow.coordinator import (
        CoordinateWorkflowUseCase,
    )

    mock_chat_uc = MagicMock()
    mock_chat_uc.execute = AsyncMock(return_value={"reply": "Hello!"})
    mock_diagnose_uc = MagicMock()
    mock_diagnose_uc.execute = AsyncMock(return_value={"root_cause": "OOM"})

    coordinator = CoordinateWorkflowUseCase(
        mcp_client=None,
        llm=None,
        chat_use_case=mock_chat_uc,
        incident_use_case=mock_diagnose_uc,
    )

    chat_res = await coordinator.execute({"event_type": "message", "route": "chat"}, {})
    assert chat_res["reply"] == "Hello!"
    assert mock_chat_uc.execute.called

    alert_res = await coordinator.execute({"event_type": "alert"}, {})
    assert alert_res["root_cause"] == "OOM"
    assert mock_diagnose_uc.execute.called


@pytest.mark.asyncio
async def test_resolve_incident_use_case_with_skill_matcher():
    from lyoko.application.incidents.use_cases.resolve import ResolveIncidentUseCase

    mock_llm = MagicMock(spec=LLMClientInterface)
    mock_runner = AsyncMock(
        return_value=(
            "ROOT_CAUSE: Container crashed\n"
            "ACTION_TAKEN: Checked logs\n"
            "VERIFICATION: Pod running\n"
            "STATUS: RESOLVED"
        )
    )
    mock_matcher = MagicMock()
    mock_matcher.format_matched_skills_context = AsyncMock(
        return_value="\n\n--- RELEVANT OPERATIONAL RUNBOOKS ---\nPod CrashLoop Guide\n---"
    )

    uc = ResolveIncidentUseCase(
        mcp_client=None,
        llm=mock_llm,
        skill_matcher_service=mock_matcher,
        runner=mock_runner,
    )

    state = {
        "event_type": "alert",
        "alert_name": "KubePodCrashLooping",
        "labels": {"reason": "CrashLoopBackOff"},
        "annotations": {},
    }
    result = await uc.execute(state, config={})

    assert result["root_cause"] == "Container crashed"
    assert result["action_taken"] == "Checked logs"
    assert result["is_resolved"] is True
    assert not result["requires_escalation"]

    # Verify that runner was called with prompt containing matched skill runbook
    called_prompt = mock_runner.call_args.kwargs["prompt"]
    assert "Pod CrashLoop Guide" in called_prompt
