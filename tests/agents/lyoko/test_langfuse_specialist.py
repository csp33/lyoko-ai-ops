"""Unit tests for Langfuse specialist agent, prompt loading, and supervisor delegation."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from lyoko.application.context.loader import build_agent_context
from lyoko.application.routing.edges import WorkflowRouteSelector
from lyoko.application.specialists.prompts import SpecialistPromptProvider
from lyoko.application.supervisor import SupervisorAgent
from lyoko.application.workflow import create_lyoko_graph
from lyoko.composition import build_domain_specialists
from lyoko.domain.interfaces.llm import LLMClientInterface
from lyoko.domain.models.incident import CoordinatorNext, SpecialistDomain


def test_langfuse_specialist_prompt_and_context():
    prompt = SpecialistPromptProvider.get_prompt("langfuse")
    assert "LLM Observability & Langfuse Specialist" in prompt
    assert "langfuse_queryMetrics" in prompt
    assert "gateway_call_tool" in prompt

    context = build_agent_context("langfuse")
    assert "HOMELAB ARCHITECTURAL CONTEXT" in context


def test_langfuse_supervisor_delegation_tools():
    mock_specialist = MagicMock()
    mock_specialist.run = AsyncMock(return_value="Trace cost: $0.0012 USD")

    supervisor = SupervisorAgent(
        specialists={"langfuse": mock_specialist},
    )

    tools = supervisor.get_delegation_tools()
    assert len(tools) == 1
    assert tools[0].name == "ask_langfuse_specialist"
    assert "Langfuse traces" in tools[0].description


@pytest.mark.asyncio
async def test_langfuse_supervisor_delegate_execution():
    mock_specialist = MagicMock()
    mock_specialist.run = AsyncMock(return_value="Trace 123 has 1500 tokens")

    supervisor = SupervisorAgent(
        specialists={"langfuse": mock_specialist},
    )

    res = await supervisor.delegate("langfuse", "Inspect trace 123")
    assert res == "Trace 123 has 1500 tokens"
    mock_specialist.run.assert_awaited_once_with(
        prompt="Inspect trace 123",
        session_id=None,
        user_id=None,
        tags=None,
        metadata=None,
        authorizer=None,
        parent_config=None,
        on_status=None,
    )


def test_coordinator_routing_to_langfuse_specialist():
    state = {
        "pending_delegation": {
            "domain": "langfuse",
            "task": "Check trace cost",
        }
    }
    next_node = WorkflowRouteSelector.choose_coordinator_next(state)
    assert next_node == CoordinatorNext.LANGFUSE
    assert next_node.value == SpecialistDomain.LANGFUSE.value


def test_create_lyoko_graph_wires_langfuse_specialist():
    mock_mcp = MagicMock()
    mock_llm = MagicMock(spec=LLMClientInterface)
    specialists = build_domain_specialists(mcp_client=mock_mcp, llm=mock_llm)

    assert "langfuse" in specialists

    graph = create_lyoko_graph(
        mcp_client=mock_mcp,
        llm=mock_llm,
        specialists=specialists,
    )
    assert graph is not None
