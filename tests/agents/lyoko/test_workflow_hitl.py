"""Human-in-the-loop behavior of the incident workflow: approve, deny, timeout, no channel."""

from unittest.mock import AsyncMock

import pytest
from lyoko.application.safety.hitl.manager import ApprovalManager
from lyoko.application.workflow.graph import create_lyoko_graph
from lyoko.domain.interfaces.mcp import ToolAuthorizer

from tests.agents.lyoko.fakes import DIAGNOSIS_ACTIONABLE, FakeMCPClient, Operator, ScriptedLLM

SCALE_ARGS = {"name": "radarr", "namespace": "media", "replicas": 2}


@pytest.fixture(autouse=True)
def _settings(monkeypatch):
    monkeypatch.setattr("lyoko.application.workflow.graph.settings.verification_delay_seconds", 0)
    monkeypatch.setattr("lyoko.application.workflow.graph.settings.auto_approved_tools", [])


def _state() -> dict:
    return {
        "event_id": "incident-abc",
        "alert_name": "KubePodCrashLooping",
        "labels": {"alertname": "KubePodCrashLooping", "namespace": "media"},
        "annotations": {},
    }


def _remediation(calls: list[dict | None]):
    async def remediate(authorize: ToolAuthorizer) -> str:
        refusal = await authorize("resources_scale", SCALE_ARGS)
        calls.append(refusal)
        return "RESULT: Scaled radarr." if refusal is None else "RESULT: Stopped."

    return remediate


@pytest.mark.asyncio
async def test_approved_change_runs_and_is_verified():
    manager = ApprovalManager()
    operator = Operator(manager, approve=True)
    chat = AsyncMock()
    chat.broadcast_approval_request = operator.broadcast_approval_request
    outcomes: list = []
    llm = ScriptedLLM(
        diagnose=DIAGNOSIS_ACTIONABLE,
        remediate=_remediation(outcomes),
        verify="RESOLVED\nPod is healthy.",
    )
    workflow = create_lyoko_graph(
        mcp_client=FakeMCPClient(), approval_manager=manager, chat_manager=chat, llm=llm
    )

    final_state = await workflow.ainvoke(_state())

    assert outcomes == [None]
    assert [a["outcome"] for a in final_state["actions"]] == ["approved"]
    assert final_state["is_resolved"] is True
    assert final_state["requires_escalation"] is False
    chat.broadcast_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_approval_request_shows_the_exact_tool_and_arguments():
    manager = ApprovalManager()
    operator = Operator(manager, approve=True)
    chat = AsyncMock()
    chat.broadcast_approval_request = operator.broadcast_approval_request
    llm = ScriptedLLM(diagnose=DIAGNOSIS_ACTIONABLE, remediate=_remediation([]), verify="RESOLVED")
    workflow = create_lyoko_graph(
        mcp_client=FakeMCPClient(), approval_manager=manager, chat_manager=chat, llm=llm
    )

    await workflow.ainvoke(_state())

    (request,) = operator.requests
    assert "Action: Scale radarr to 2 replicas in namespace 'media'." in request.details
    assert "Plan: 1. Call resources_scale for deployment radarr." in request.details
    assert "resources_scale" in request.details
    assert '"replicas": 2' in request.details
    assert "KubePodCrashLooping" in request.details
    assert request.session_id == "incident-abc"
    assert request.incident_id.startswith("incident-abc.")
    assert [a.label for a in request.actions] == ["✅ Approve", "❌ Deny"]


@pytest.mark.asyncio
async def test_denied_change_does_not_run_and_escalates():
    manager = ApprovalManager()
    operator = Operator(manager, approve=False, reason="Manual denial by operator")
    chat = AsyncMock()
    chat.broadcast_approval_request = operator.broadcast_approval_request
    outcomes: list = []
    llm = ScriptedLLM(diagnose=DIAGNOSIS_ACTIONABLE, remediate=_remediation(outcomes))
    workflow = create_lyoko_graph(
        mcp_client=FakeMCPClient(), approval_manager=manager, chat_manager=chat, llm=llm
    )

    final_state = await workflow.ainvoke(_state())

    assert outcomes[0] is not None
    assert "did not approve" in outcomes[0]
    assert final_state["requires_escalation"] is True
    assert final_state["is_resolved"] is False
    assert "aborted" in final_state["action_taken"].lower()
    assert "Manual denial by operator" in final_state["action_taken"]
    assert [a["outcome"] for a in final_state["actions"]] == ["denied"]
    assert "verify" not in llm.prompts


@pytest.mark.asyncio
async def test_without_an_approval_channel_changes_are_refused_not_auto_approved():
    outcomes: list = []
    llm = ScriptedLLM(diagnose=DIAGNOSIS_ACTIONABLE, remediate=_remediation(outcomes))
    workflow = create_lyoko_graph(mcp_client=FakeMCPClient(), llm=llm)

    final_state = await workflow.ainvoke(_state())

    assert outcomes[0] is not None
    assert "no approval channel" in outcomes[0]
    assert final_state["requires_escalation"] is True
    assert [a["outcome"] for a in final_state["actions"]] == ["refused"]


@pytest.mark.asyncio
async def test_each_change_gets_its_own_approval():
    manager = ApprovalManager()
    operator = Operator(manager, approve=True)
    chat = AsyncMock()
    chat.broadcast_approval_request = operator.broadcast_approval_request

    async def two_changes(authorize: ToolAuthorizer) -> str:
        await authorize("resources_scale", SCALE_ARGS)
        await authorize("pods_delete", {"name": "radarr-1"})
        return "RESULT: Scaled and restarted."

    llm = ScriptedLLM(diagnose=DIAGNOSIS_ACTIONABLE, remediate=two_changes, verify="RESOLVED")
    workflow = create_lyoko_graph(
        mcp_client=FakeMCPClient(), approval_manager=manager, chat_manager=chat, llm=llm
    )

    final_state = await workflow.ainvoke(_state())

    assert len(operator.requests) == 2
    assert len({r.incident_id for r in operator.requests}) == 2
    assert [a["tool"] for a in final_state["actions"]] == ["resources_scale", "pods_delete"]
