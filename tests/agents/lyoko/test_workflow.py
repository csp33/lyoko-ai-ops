"""Unit tests for the LYOKO autonomous incident resolution workflow."""

from unittest.mock import AsyncMock

import pytest
from lyoko.application.workflow.graph import create_lyoko_graph
from lyoko.domain.interfaces.mcp import ToolAuthorizer

from tests.agents.lyoko.fakes import (
    RESOLUTION_ESCALATED,
    RESOLUTION_RESOLVED,
    FakeMCPClient,
    ScriptedLLM,
)

SCALE_ARGS = {"name": "radarr", "namespace": "media", "replicas": 0}


@pytest.fixture(autouse=True)
def fast_verification(monkeypatch):
    monkeypatch.setattr("lyoko.config.settings.verification_delay_seconds", 0)
    monkeypatch.setattr("lyoko.config.settings.auto_approved_tools", [])


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
    return RESOLUTION_RESOLVED


@pytest.mark.asyncio
async def test_auto_approved_tool_fixes_and_resolves(monkeypatch):
    monkeypatch.setattr("lyoko.config.settings.auto_approved_tools", ["resources_scale"])
    llm = ScriptedLLM(incident=_scale_with_auto_approval)
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
    llm = ScriptedLLM(incident=RESOLUTION_ESCALATED)
    workflow = create_lyoko_graph(mcp_client=FakeMCPClient(), llm=llm)

    await workflow.ainvoke(
        _state({"alertname": "UnifiApOffline", "site": "home", "device": "ap-living-room"})
    )

    prompt = llm.prompts["incident"]
    assert "UnifiApOffline" in prompt
    assert "ap-living-room" in prompt
    assert "radarr is crashlooping" in prompt


@pytest.mark.asyncio
async def test_non_actionable_escalates_without_modifications():
    llm = ScriptedLLM(incident=RESOLUTION_ESCALATED)
    workflow = create_lyoko_graph(mcp_client=FakeMCPClient(), llm=llm)

    final_state = await workflow.ainvoke(_state())

    assert "credentials" in final_state["root_cause"]
    assert final_state["requires_escalation"] is True
    assert final_state["is_resolved"] is False
    assert set(llm.prompts) == {"incident"}


@pytest.mark.asyncio
async def test_agent_failure_is_escalated_not_raised():
    async def broken(authorize: ToolAuthorizer) -> str:
        raise RuntimeError("model unavailable")

    llm = ScriptedLLM(incident=broken)
    workflow = create_lyoko_graph(mcp_client=FakeMCPClient(), llm=llm)

    final_state = await workflow.ainvoke(_state())

    assert "Resolution failed: model unavailable" in final_state["root_cause"]
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
    monkeypatch.setattr("lyoko.config.settings.max_agent_steps", 7)
    llm = ScriptedLLM(incident=RESOLUTION_ESCALATED)
    workflow = create_lyoko_graph(mcp_client=FakeMCPClient(), llm=llm)

    await workflow.ainvoke(_state())

    assert llm.max_steps == [7]


@pytest.mark.asyncio
async def test_report_lists_what_the_gate_allowed():
    chat = AsyncMock()
    llm = ScriptedLLM(incident=RESOLUTION_ESCALATED)
    workflow = create_lyoko_graph(mcp_client=FakeMCPClient(), chat_manager=chat, llm=llm)

    await workflow.ainvoke(_state({"alertname": "UnifiApOffline", "device": "ap-living-room"}))

    chat.broadcast_message.assert_awaited_once()
    delivered = [call.kwargs["text"] for call in chat.broadcast_message.await_args_list]
    if chat.edit_message.await_args is not None:
        delivered.append(chat.edit_message.await_args.kwargs["text"])
    text = "\n".join(delivered)

    assert "UnifiApOffline" in text
    assert "device=ap-living-room" in text
    assert "ESCALATED" in text


@pytest.mark.asyncio
async def test_incident_workflow_live_status_updates(monkeypatch):
    from lyoko.domain.models.chat import SentMessage

    monkeypatch.setattr("lyoko.config.settings.auto_approved_tools", ["resources_scale"])
    chat = AsyncMock()
    chat.broadcast_message.return_value = [SentMessage(chat_id="12345", message_id="777")]
    chat.edit_message.return_value = [SentMessage(chat_id="12345", message_id="777")]

    llm = ScriptedLLM(incident=_scale_with_auto_approval)
    workflow = create_lyoko_graph(mcp_client=FakeMCPClient(), chat_manager=chat, llm=llm)

    state = _state({"alertname": "KubePodCrashLooping", "namespace": "media", "pod": "radarr-0"})
    state["chat_id"] = "12345"
    final_state = await workflow.ainvoke(state)

    assert final_state["is_resolved"] is True
    assert final_state["progress_message_id"] == "777"
    assert final_state["progress_chat_id"] == "12345"

    chat.broadcast_message.assert_awaited_once()
    initial_text = chat.broadcast_message.await_args.kwargs["text"]
    assert (
        "Investigating" in initial_text or "Status" in initial_text or "DIAGNOSING" in initial_text
    )

    assert chat.edit_message.await_count >= 1
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
