"""Unit tests for the LYOKO incident workflow (diagnose, remediate, verify, notify)."""

from unittest.mock import AsyncMock

import pytest
from lyoko.application.workflow.graph import create_lyoko_graph
from lyoko.domain.interfaces.mcp import ToolAuthorizer

from tests.agents.lyoko.fakes import (
    DIAGNOSIS_ACTIONABLE,
    DIAGNOSIS_NOT_ACTIONABLE,
    FakeMCPClient,
    ScriptedLLM,
)

SCALE_ARGS = {"name": "radarr", "namespace": "media", "replicas": 0}


@pytest.fixture(autouse=True)
def fast_verification(monkeypatch):
    monkeypatch.setattr("lyoko.application.workflow.graph.settings.verification_delay_seconds", 0)
    monkeypatch.setattr("lyoko.application.workflow.graph.settings.auto_approved_tools", [])


def _state(labels: dict[str, str] | None = None) -> dict:
    labels = labels or {"alertname": "KubePodCrashLooping", "namespace": "media"}
    return {
        "event_id": "incident-abc",
        "alert_name": labels["alertname"],
        "labels": labels,
        "annotations": {"summary": "radarr is crashlooping"},
    }


async def _scale_with_auto_approval(authorize: ToolAuthorizer) -> str:
    assert await authorize("resources_scale", SCALE_ARGS) is None
    return "RESULT: Scaled radarr."


async def _read_only_check(authorize: ToolAuthorizer) -> str:
    assert await authorize("pods_get", {"name": "radarr"}) is None
    return "RESOLVED\nThe pod is Running with 0 restarts."


@pytest.mark.asyncio
async def test_auto_approved_tool_fixes_and_resolves(monkeypatch):
    monkeypatch.setattr(
        "lyoko.application.workflow.graph.settings.auto_approved_tools", ["resources_scale"]
    )
    llm = ScriptedLLM(
        diagnose=DIAGNOSIS_ACTIONABLE,
        remediate=_scale_with_auto_approval,
        verify=_read_only_check,
    )
    workflow = create_lyoko_graph(mcp_client=FakeMCPClient(), llm=llm)

    final_state = await workflow.ainvoke(_state())

    assert "OOMKilled" in final_state["root_cause"]
    assert final_state["action_taken"] == "Scaled radarr."
    assert [a["outcome"] for a in final_state["actions"]] == ["auto_approved"]
    assert final_state["is_resolved"] is True
    assert final_state["requires_escalation"] is False
    assert "Running with 0 restarts" in final_state["verification"]


@pytest.mark.asyncio
async def test_alert_context_reaches_the_agent_without_kubernetes_assumptions():
    llm = ScriptedLLM(diagnose=DIAGNOSIS_NOT_ACTIONABLE)
    workflow = create_lyoko_graph(mcp_client=FakeMCPClient(), llm=llm)

    await workflow.ainvoke(
        _state({"alertname": "UnifiApOffline", "site": "home", "device": "ap-living-room"})
    )

    prompt = llm.prompts["diagnose"]
    assert "UnifiApOffline" in prompt
    assert "ap-living-room" in prompt
    assert "radarr is crashlooping" in prompt


@pytest.mark.asyncio
async def test_non_actionable_diagnosis_escalates_without_remediating():
    llm = ScriptedLLM(diagnose=DIAGNOSIS_NOT_ACTIONABLE)
    workflow = create_lyoko_graph(mcp_client=FakeMCPClient(), llm=llm)

    final_state = await workflow.ainvoke(_state())

    assert "credentials" in final_state["root_cause"]
    assert "requires human inspection" in final_state["action_taken"]
    assert final_state["requires_escalation"] is True
    assert final_state["is_resolved"] is False
    assert set(llm.prompts) == {"diagnose"}


@pytest.mark.asyncio
async def test_diagnosis_cannot_change_anything():
    refusals: list[str | None] = []

    async def try_to_mutate(authorize: ToolAuthorizer) -> str:
        refusals.append(await authorize("resources_scale", SCALE_ARGS))
        return DIAGNOSIS_ACTIONABLE

    llm = ScriptedLLM(diagnose=try_to_mutate, remediate="RESULT: nothing", verify="RESOLVED")
    workflow = create_lyoko_graph(mcp_client=FakeMCPClient(), llm=llm)

    await workflow.ainvoke(_state())

    assert len(refusals) == 1
    assert refusals[0] is not None
    assert "read-only" in refusals[0]


@pytest.mark.asyncio
async def test_run_without_any_change_is_not_reported_as_fixed():
    llm = ScriptedLLM(
        diagnose=DIAGNOSIS_ACTIONABLE,
        remediate="RESULT: Everything already looked fine, I changed nothing.",
    )
    workflow = create_lyoko_graph(mcp_client=FakeMCPClient(), llm=llm)

    final_state = await workflow.ainvoke(_state())

    assert final_state["requires_escalation"] is True
    assert final_state["is_resolved"] is False
    assert "I changed nothing" in final_state["action_taken"]
    assert "verify" not in llm.prompts


@pytest.mark.asyncio
async def test_unresolved_after_fix_is_reported_as_unresolved(monkeypatch):
    monkeypatch.setattr(
        "lyoko.application.workflow.graph.settings.auto_approved_tools", ["resources_scale"]
    )
    llm = ScriptedLLM(
        diagnose=DIAGNOSIS_ACTIONABLE,
        remediate=_scale_with_auto_approval,
        verify="UNRESOLVED\nThe pod is still crashlooping.",
    )
    workflow = create_lyoko_graph(mcp_client=FakeMCPClient(), llm=llm)

    final_state = await workflow.ainvoke(_state())

    assert final_state["is_resolved"] is False
    assert "still crashlooping" in final_state["verification"]


@pytest.mark.asyncio
async def test_agent_failure_is_escalated_not_raised():
    async def broken(authorize: ToolAuthorizer) -> str:
        raise RuntimeError("model unavailable")

    llm = ScriptedLLM(diagnose=broken)
    workflow = create_lyoko_graph(mcp_client=FakeMCPClient(), llm=llm)

    final_state = await workflow.ainvoke(_state())

    assert "Investigation failed: model unavailable" in final_state["root_cause"]
    assert final_state["requires_escalation"] is True


@pytest.mark.asyncio
async def test_without_llm_the_incident_is_escalated():
    workflow = create_lyoko_graph(mcp_client=FakeMCPClient(), llm=None)

    final_state = await workflow.ainvoke(_state())

    assert "no LLM provider" in final_state["root_cause"]
    assert final_state["requires_escalation"] is True
    assert final_state["is_resolved"] is False


@pytest.mark.asyncio
async def test_agent_runs_are_bounded(monkeypatch):
    monkeypatch.setattr("lyoko.application.workflow.graph.settings.max_agent_steps", 7)
    llm = ScriptedLLM(diagnose=DIAGNOSIS_NOT_ACTIONABLE)
    workflow = create_lyoko_graph(mcp_client=FakeMCPClient(), llm=llm)

    await workflow.ainvoke(_state())

    assert llm.max_steps == [7]


@pytest.mark.asyncio
async def test_report_lists_what_the_gate_allowed():
    chat = AsyncMock()
    llm = ScriptedLLM(diagnose=DIAGNOSIS_NOT_ACTIONABLE)
    workflow = create_lyoko_graph(mcp_client=FakeMCPClient(), chat_manager=chat, llm=llm)

    await workflow.ainvoke(_state({"alertname": "UnifiApOffline", "device": "ap-living-room"}))

    # The live status message is broadcast once, then edited as the incident advances; the final
    # edit carries the incident report. Collect every delivered text so the assertion covers both
    # the created progress message and the edited final report.
    chat.broadcast_message.assert_awaited_once()
    delivered = [call.kwargs["text"] for call in chat.broadcast_message.await_args_list]
    if chat.edit_message.await_args is not None:
        delivered.append(chat.edit_message.await_args.kwargs["text"])
    text = "\n".join(delivered)

    assert "UnifiApOffline" in text
    assert "device=ap-living-room" in text
    assert "ESCALATED" in text


@pytest.mark.asyncio
async def test_workflow_works_with_a_checkpointer():
    from langgraph.checkpoint.memory import MemorySaver

    llm = ScriptedLLM(diagnose=DIAGNOSIS_NOT_ACTIONABLE)
    workflow = create_lyoko_graph(mcp_client=FakeMCPClient(), checkpointer=MemorySaver(), llm=llm)
    config = {"configurable": {"thread_id": "incident-abc"}}

    await workflow.ainvoke(_state(), config=config)

    saved = await workflow.aget_state(config)
    assert "credentials" in saved.values["root_cause"]


@pytest.mark.asyncio
async def test_incident_workflow_live_status_updates(monkeypatch):
    from lyoko.domain.models.chat import SentMessage

    monkeypatch.setattr(
        "lyoko.application.workflow.graph.settings.auto_approved_tools", ["resources_scale"]
    )
    chat = AsyncMock()
    chat.broadcast_message.return_value = [SentMessage(chat_id="12345", message_id="777")]
    chat.edit_message.return_value = [SentMessage(chat_id="12345", message_id="777")]

    llm = ScriptedLLM(
        diagnose=DIAGNOSIS_ACTIONABLE,
        remediate=_scale_with_auto_approval,
        verify=_read_only_check,
    )
    workflow = create_lyoko_graph(mcp_client=FakeMCPClient(), chat_manager=chat, llm=llm)

    state = _state({"alertname": "KubePodCrashLooping", "namespace": "media", "pod": "radarr-0"})
    state["chat_id"] = "12345"
    final_state = await workflow.ainvoke(state)

    assert final_state["is_resolved"] is True
    assert final_state["progress_message_id"] == "777"
    assert final_state["progress_chat_id"] == "12345"

    # Verify initial progress broadcast
    chat.broadcast_message.assert_awaited_once()
    initial_text = chat.broadcast_message.await_args.kwargs["text"]
    assert "Phase: Diagnose" in initial_text

    # Verify progressive edits (after diagnose, after remediate, and final report on notify)
    assert chat.edit_message.await_count >= 3
    final_edit_text = chat.edit_message.await_args_list[-1].kwargs["text"]
    assert "📋 **LYOKO Incident Report**" in final_edit_text
    assert "**Status:** ✅ RESOLVED" in final_edit_text


def test_incident_context_formats_correlated_alerts():
    from lyoko.application.context.formatter import IncidentContextFormatter

    state = {
        "alert_name": "KubeNodeNotReady",
        "labels": {"alertname": "KubeNodeNotReady", "node": "worker-1"},
        "annotations": {"summary": "Node worker-1 not ready"},
        "correlated_alerts": [
            {"alertname": "TargetDown", "namespace": "monitoring", "pod": "prometheus-0"},
            {"alertname": "KubePodCrashLooping", "namespace": "default", "pod": "api-123"},
        ],
    }
    context = IncidentContextFormatter.format_context(state)
    assert "KubeNodeNotReady" in context
    assert "Correlated / Cascade Alerts (2):" in context
    assert "- TargetDown (namespace: monitoring, pod: prometheus-0)" in context
    assert "- KubePodCrashLooping (namespace: default, pod: api-123)" in context
