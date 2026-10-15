"""Unit tests for the autonomous incident resolution workflow."""

from unittest.mock import AsyncMock

import pytest
from lyoko.application.safety.hitl.manager import ApprovalManager
from lyoko.domain.interfaces.mcp import ToolAuthorizer

from tests.agents.lyoko.fakes import FakeMCPClient, Operator, ScriptedLLM, create_lyoko_graph

SCALE_ARGS = {"name": "radarr", "namespace": "media", "replicas": 0}


@pytest.fixture(autouse=True)
def configure_hermes(monkeypatch):
    monkeypatch.setattr("lyoko.config.settings.verification_delay_seconds", 0)
    monkeypatch.setattr("lyoko.config.settings.auto_approved_tools", [])


def _state(labels: dict[str, str] | None = None) -> dict:
    labels = labels or {"alertname": "KubePodCrashLooping", "namespace": "media"}
    return {
        "event_id": "incident-hermes-123",
        "alert_name": labels["alertname"],
        "labels": labels,
        "annotations": {"summary": "radarr is crashlooping"},
    }


def _build_workflow(
    llm: ScriptedLLM,
    *,
    mcp_client: FakeMCPClient | None = None,
    approval_manager: ApprovalManager | None = None,
    chat_manager: AsyncMock | None = None,
):
    client = mcp_client or FakeMCPClient()
    return create_lyoko_graph(
        mcp_client=client,
        approval_manager=approval_manager,
        chat_manager=chat_manager,
        llm=llm,
    )


@pytest.mark.asyncio
async def test_hermes_resolves_incident_in_single_turn_with_auto_approved_tool(monkeypatch):
    monkeypatch.setattr("lyoko.config.settings.auto_approved_tools", ["resources_scale"])

    async def hermes_agent_behavior(authorize: ToolAuthorizer) -> str:
        # Diagnostic read
        assert await authorize("pods_get", {"name": "radarr"}) is None
        # Auto-approved remediation write
        assert await authorize("resources_scale", SCALE_ARGS) is None
        # Verification read
        assert await authorize("pods_get", {"name": "radarr"}) is None
        return (
            "ROOT_CAUSE: Pod was OOMKilled due to memory limit.\n"
            "ACTION_TAKEN: Scaled radarr deployment replicas.\n"
            "VERIFICATION: Pod is healthy and running normally.\n"
            "STATUS: RESOLVED"
        )

    llm = ScriptedLLM(incident=hermes_agent_behavior)
    workflow = _build_workflow(llm)

    final_state = await workflow.ainvoke(_state())

    assert "Pod was OOMKilled" in final_state["root_cause"]
    assert final_state["action_taken"] == "Scaled radarr deployment replicas."
    assert final_state["is_resolved"] is True
    assert final_state["requires_escalation"] is False
    assert "Pod is healthy" in final_state["verification"]
    assert len(final_state["actions"]) == 1
    # Exactly one single turn for incident execution, no separate remediate or verify LLM calls
    assert llm.calls == ["incident"]


@pytest.mark.asyncio
async def test_hermes_waits_for_operator_approval_on_write_tool():
    manager = ApprovalManager()
    operator = Operator(manager, approve=True)
    chat = AsyncMock()
    chat.broadcast_approval_request = operator.broadcast_approval_request

    async def hermes_behavior(authorize: ToolAuthorizer) -> str:
        assert await authorize("pods_get", {"name": "radarr"}) is None
        # Write tool prompts the operator
        auth_res = await authorize("resources_scale", SCALE_ARGS)
        assert auth_res is None  # Approved
        return (
            "ROOT_CAUSE: Memory leak in worker.\n"
            "ACTION_TAKEN: Restarted radarr.\n"
            "VERIFICATION: Clean restart, memory usage normal.\n"
            "STATUS: RESOLVED"
        )

    llm = ScriptedLLM(incident=hermes_behavior)
    workflow = _build_workflow(
        llm,
        approval_manager=manager,
        chat_manager=chat,
    )

    final_state = await workflow.ainvoke(_state())

    assert len(operator.requests) == 1
    assert "resources_scale" in operator.requests[0].title
    assert final_state["is_resolved"] is True
    assert final_state["requires_escalation"] is False
    assert llm.calls == ["incident"]


@pytest.mark.asyncio
async def test_hermes_escalates_when_operator_denies_write_tool():
    manager = ApprovalManager()
    operator = Operator(manager, approve=False, reason="Not approved right now")
    chat = AsyncMock()
    chat.broadcast_approval_request = operator.broadcast_approval_request

    async def hermes_behavior(authorize: ToolAuthorizer) -> str:
        auth_res = await authorize("resources_scale", SCALE_ARGS)
        assert "Not approved" in str(auth_res)  # Denied
        return (
            "ROOT_CAUSE: Deployment issue.\n"
            "ACTION_TAKEN: Attempted scale but operator denied.\n"
            "VERIFICATION: None.\n"
            "STATUS: ESCALATED"
        )

    llm = ScriptedLLM(incident=hermes_behavior)
    workflow = _build_workflow(
        llm,
        approval_manager=manager,
        chat_manager=chat,
    )

    final_state = await workflow.ainvoke(_state())

    assert len(operator.requests) == 1
    assert final_state["is_resolved"] is False
    assert final_state["requires_escalation"] is True
    assert "Remediation aborted: Not approved right now" in final_state["action_taken"]
    assert llm.calls == ["incident"]


@pytest.mark.asyncio
async def test_hermes_does_not_inject_semantic_memory_in_prompt():
    async def behavior(authorize: ToolAuthorizer) -> str:
        return "ROOT_CAUSE: Normal.\nACTION_TAKEN: None.\nVERIFICATION: Healthy.\nSTATUS: RESOLVED"

    llm = ScriptedLLM(incident=behavior)
    workflow = _build_workflow(llm)
    await workflow.ainvoke(_state())

    prompt = llm.prompts["incident"]
    assert "PRIOR OPERATOR FEEDBACK & LESSONS LEARNED" not in prompt
