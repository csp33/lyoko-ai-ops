"""Unit tests for newly introduced application UseCase classes."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from lyoko.application.incidents.use_cases.notify import (
    NotifyIncidentReportUseCase,
)
from lyoko.application.memory.use_cases.feedback import RecordFeedbackUseCase
from lyoko.application.memory.use_cases.retrieve import RetrieveMemoryLessonsUseCase
from lyoko.application.workflow.routing.route_event import RouteEventUseCase
from lyoko.domain.interfaces.llm import LLMClientInterface
from lyoko.domain.models.memory import FeedbackRequest, MemoryEntry, MemoryQueryResult
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
async def test_record_feedback_use_case():
    mock_repo = AsyncMock()
    mock_repo.save_memory.return_value = 42
    mock_embeddings = AsyncMock()
    mock_embeddings.embed_text.return_value = [0.1] * 1536

    use_case = RecordFeedbackUseCase(mock_repo, mock_embeddings)

    req = FeedbackRequest(
        namespace="default",
        service_name="web",
        incident_pattern="CrashLoop",
        operator_feedback="restart service first",
    )
    mem_id = await use_case.execute_from_request(req)
    assert mem_id == 42
    assert mock_repo.save_memory.called

    chat_mem_id, svc, ns = await use_case.execute_from_chat_command(
        feedback_content="compact logs first",
        reply_to_text="Namespace: prod\nPod: web-api-1234\nAlert: HighMemory",
    )
    assert chat_mem_id == 42
    assert svc == "web"
    assert ns == "prod"


@pytest.mark.asyncio
async def test_retrieve_memory_use_case():
    mock_repo = AsyncMock()
    mem = MemoryEntry(
        namespace="default",
        service_name="app",
        incident_pattern="OOM",
        operator_feedback="raise limits",
    )
    mock_repo.search_memories.return_value = [MemoryQueryResult(memory=mem, similarity=0.85)]

    use_case = RetrieveMemoryLessonsUseCase(mock_repo)
    matched, lessons = await use_case.execute_for_incident(
        {"alert_name": "OOM", "labels": {"namespace": "default"}}
    )
    assert len(matched) == 1
    assert "raise limits" in lessons

    chat_lessons = await use_case.execute_for_chat("app memory issues")
    assert "raise limits" in chat_lessons


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
async def test_execute_specialist_task_use_case():
    from lyoko.application.agents.task_runner import (
        ExecuteSpecialistTaskUseCase,
    )

    specialist = MagicMock()
    specialist.run = AsyncMock(return_value="K8s pod restarted")
    uc = ExecuteSpecialistTaskUseCase(domain="kubernetes", specialist=specialist)

    # Without matching pending delegation
    res_none = await uc.execute({"pending_delegation": None}, {})
    assert res_none == {"pending_delegation": None}

    # With matching pending delegation
    res_match = await uc.execute(
        {
            "pending_delegation": {"domain": "kubernetes", "task": "Check pod"},
            "delegation_history": [],
        },
        {},
    )
    assert res_match["pending_delegation"] is None
    assert len(res_match["delegation_history"]) == 1
    assert res_match["delegation_history"][0]["response"] == "K8s pod restarted"


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
        diagnose_use_case=mock_diagnose_uc,
    )

    chat_res = await coordinator.execute({"event_type": "message", "route": "chat"}, {})
    assert chat_res["reply"] == "Hello!"
    assert mock_chat_uc.execute.called

    alert_res = await coordinator.execute({"event_type": "alert"}, {})
    assert alert_res["root_cause"] == "OOM"
    assert mock_diagnose_uc.execute.called


@pytest.mark.asyncio
async def test_diagnose_incident_use_case_with_skill_matcher():
    from lyoko.application.incidents.use_cases.diagnose import DiagnoseIncidentUseCase

    mock_llm = MagicMock(spec=LLMClientInterface)
    mock_runner = AsyncMock(
        return_value="ROOT_CAUSE: Container crashed\nACTIONABLE: yes\nPLAN: Inspect container logs"
    )
    mock_matcher = MagicMock()
    mock_matcher.format_matched_skills_context = AsyncMock(
        return_value="\n\n--- RELEVANT OPERATIONAL RUNBOOKS ---\nPod CrashLoop Guide\n---"
    )

    uc = DiagnoseIncidentUseCase(
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
    assert result["plan"] == "Inspect container logs"
    assert not result["requires_escalation"]

    # Verify that runner was called with prompt containing matched skill runbook
    called_prompt = mock_runner.call_args.kwargs["prompt"]
    assert "Pod CrashLoop Guide" in called_prompt
