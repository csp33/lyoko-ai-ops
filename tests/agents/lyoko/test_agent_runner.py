"""Unit tests for AgentRunner class."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from lyoko.application.agents.runner import AgentRunner, status_callback
from lyoko.application.safety.tool_gate import GateMode, make_gate
from lyoko.domain.interfaces.llm import LLMClientInterface
from lyoko.domain.interfaces.mcp import MCPClientInterface


def test_status_callback_extraction():
    sentinel = object()
    assert status_callback({"configurable": {"on_status": sentinel}}) is sentinel
    assert status_callback({"configurable": {}}) is None
    assert status_callback(None) is None
    assert status_callback("not-a-dict") is None

    assert (
        AgentRunner.extract_status_callback({"configurable": {"on_status": sentinel}}) is sentinel
    )


def test_agent_runner_initialization():
    mock_mcp = MagicMock(spec=MCPClientInterface)
    mock_llm = MagicMock(spec=LLMClientInterface)
    runner = AgentRunner(mcp_client=mock_mcp, llm=mock_llm, default_max_steps=10)

    assert runner.mcp_client is mock_mcp
    assert runner.llm is mock_llm
    assert runner.default_max_steps == 10


@pytest.mark.asyncio
async def test_agent_runner_raises_without_llm():
    runner = AgentRunner()
    gate = make_gate({}, GateMode.READ_ONLY)
    with pytest.raises(ValueError, match="llm client is required"):
        await runner.run(
            state={},
            gate=gate,
            phase="test",
            system_prompt="sys",
            prompt="user prompt",
        )


@pytest.mark.asyncio
async def test_agent_runner_run_executes_llm():
    mock_llm = MagicMock(spec=LLMClientInterface)
    mock_llm.chat = AsyncMock(return_value="Agent response")
    mock_mcp = MagicMock(spec=MCPClientInterface)
    mock_mcp.get_langchain_tools.return_value = ["mock_tool"]

    runner = AgentRunner(mcp_client=mock_mcp, llm=mock_llm, default_max_steps=5)
    gate = make_gate({"event_id": "evt-1"}, GateMode.READ_ONLY)
    sentinel_status = object()
    config = {"configurable": {"on_status": sentinel_status}}

    state = {
        "session_id": "sess-123",
        "trace_id": "tr-456",
    }

    result = await runner.run(
        state=state,
        gate=gate,
        config=config,
        phase="chat",
        system_prompt="You are LYOKO",
        prompt="Check sonarr pod",
    )

    assert result == "Agent response"
    mock_mcp.get_langchain_tools.assert_called_once_with(authorizer=gate.authorize)
    mock_llm.chat.assert_awaited_once()

    call_kwargs = mock_llm.chat.await_args.kwargs
    assert call_kwargs["prompt"] == "Check sonarr pod"
    assert call_kwargs["system_prompt"] == "You are LYOKO"
    assert call_kwargs["tools"] == ["mock_tool"]
    assert call_kwargs["session_id"] == "sess-123"
    assert call_kwargs["trace_name"] == "chat-agent"
    assert call_kwargs["tags"] == ["phase:chat"]
    assert call_kwargs["max_steps"] == 5
    assert call_kwargs["on_status"] is sentinel_status
    assert call_kwargs["metadata"] == {"trace_id": "tr-456"}


@pytest.mark.asyncio
async def test_agent_runner_callable():
    mock_llm = MagicMock(spec=LLMClientInterface)
    mock_llm.chat = AsyncMock(return_value="Callable response")

    runner = AgentRunner(llm=mock_llm)
    gate = make_gate({}, GateMode.READ_ONLY)

    result = await runner(
        state={},
        gate=gate,
        phase="incident",
        system_prompt="sys",
        prompt="fix issue",
    )

    assert result == "Callable response"
    assert mock_llm.chat.await_args.kwargs["trace_name"] == "incident-agent"


@pytest.mark.asyncio
async def test_agent_runner_parameter_overrides():
    default_llm = MagicMock(spec=LLMClientInterface)
    override_llm = MagicMock(spec=LLMClientInterface)
    override_llm.chat = AsyncMock(return_value="Override response")

    runner = AgentRunner(llm=default_llm)
    gate = make_gate({}, GateMode.READ_ONLY)

    result = await runner.run(
        state={},
        gate=gate,
        llm=override_llm,
        phase="chat",
        system_prompt="sys",
        prompt="test",
    )

    assert result == "Override response"
    default_llm.chat.assert_not_called()
    override_llm.chat.assert_awaited_once()
