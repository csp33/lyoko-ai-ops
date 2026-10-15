"""Unit tests for OpenAILLMAdapter infrastructure adapter."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import ToolMessage
from lyoko.infrastructure.llm.openai import OpenAILLMAdapter


def test_openai_llm_adapter_uses_responses_api_by_default():
    """The adapter defaults to the Responses API and can be switched back to Chat Completions."""
    assert OpenAILLMAdapter(api_key="sk-test").client.use_responses_api is True
    assert (
        OpenAILLMAdapter(api_key="sk-test", use_responses_api=False).client.use_responses_api
        is False
    )


@pytest.mark.asyncio
async def test_openai_llm_adapter_chat():
    """Verify chat invocation delegates to ChatOpenAI with proper messages."""
    adapter = OpenAILLMAdapter(api_key="sk-test", model_name="gpt-4o-mini")
    mock_client = AsyncMock()
    mock_response = MagicMock()
    mock_response.content = "Everything is running smoothly."
    mock_client.ainvoke.return_value = mock_response
    adapter._client = mock_client

    result = await adapter.chat(prompt="How is the cluster?", system_prompt="You are LYOKO.")
    assert result == "Everything is running smoothly."
    mock_client.ainvoke.assert_awaited_once()

    messages = mock_client.ainvoke.call_args[0][0]
    assert len(messages) == 2
    assert messages[0].content == "You are LYOKO."
    assert messages[1].content == "How is the cluster?"


@pytest.mark.asyncio
@patch("lyoko.infrastructure.llm.openai.get_langfuse_trace_config")
async def test_openai_llm_adapter_with_langfuse_callback(mock_get_config):
    """Verify Langfuse trace config is attached when available."""
    mock_get_config.return_value = {
        "callbacks": ["mock_cb"],
        "metadata": {"langfuse_session_id": "telegram-123"},
    }

    adapter = OpenAILLMAdapter(api_key="sk-test")
    mock_client = AsyncMock()
    mock_response = MagicMock()
    mock_response.content = "All green."
    mock_client.ainvoke.return_value = mock_response
    adapter._client = mock_client

    result = await adapter.chat(
        prompt="Status check",
        session_id="telegram-123",
        user_id="user-456",
    )
    assert result == "All green."
    config = mock_client.ainvoke.call_args[1].get("config")
    assert config == {"callbacks": ["mock_cb"], "metadata": {"langfuse_session_id": "telegram-123"}}
    mock_get_config.assert_called_once_with(
        session_id="telegram-123",
        user_id="user-456",
        trace_name="telegram-chat-interaction",
        tags=["telegram", "chat-agent"],
        metadata=None,
    )


@pytest.mark.asyncio
async def test_openai_llm_adapter_bounds_agent_steps():
    """max_steps limits the tool-use loop iterations and falls back to a final summary."""
    tool_call_msg = MagicMock()
    tool_call_msg.tool_calls = [{"name": "mock_tool", "args": {}, "id": "call_1"}]
    mock_bound_client = AsyncMock()
    mock_bound_client.ainvoke.return_value = tool_call_msg

    adapter = OpenAILLMAdapter(api_key="sk-test")
    mock_client = MagicMock()
    mock_client.bind_tools.return_value = mock_bound_client
    mock_summary_response = MagicMock()
    mock_summary_response.content = "Summary of findings after reaching maximum steps."
    mock_client.ainvoke = AsyncMock(return_value=mock_summary_response)
    adapter._client = mock_client

    mock_tool = MagicMock()
    mock_tool.name = "mock_tool"
    mock_tool.ainvoke = AsyncMock(return_value="tool_result")

    result = await adapter.chat(prompt="Investigate", tools=[mock_tool], max_steps=3)

    assert mock_bound_client.ainvoke.await_count == 3
    mock_client.ainvoke.assert_awaited_once()
    assert result == "Summary of findings after reaching maximum steps."


@pytest.mark.asyncio
@patch("lyoko.infrastructure.llm.openai.get_langfuse_trace_config")
async def test_openai_llm_adapter_step_bound_keeps_trace_config(mock_get_config):
    mock_cb = BaseCallbackHandler()
    mock_get_config.return_value = {"callbacks": [mock_cb]}
    resp_msg = MagicMock()
    resp_msg.tool_calls = []
    resp_msg.content = "done"

    mock_bound_client = AsyncMock()
    mock_bound_client.ainvoke.return_value = resp_msg

    adapter = OpenAILLMAdapter(api_key="sk-test")
    mock_client = MagicMock()
    mock_client.bind_tools.return_value = mock_bound_client
    adapter._client = mock_client

    result = await adapter.chat(prompt="Investigate", tools=[MagicMock()], max_steps=3)
    assert result == "done"
    mock_get_config.assert_called_once()


@pytest.mark.asyncio
async def test_nested_agent_joins_the_parent_trace_instead_of_starting_one():
    resp_msg = MagicMock()
    resp_msg.tool_calls = []
    resp_msg.content = "done"

    mock_bound_client = AsyncMock()
    mock_bound_client.ainvoke.return_value = resp_msg

    adapter = OpenAILLMAdapter(api_key="sk-test")
    mock_client = MagicMock()
    mock_client.bind_tools.return_value = mock_bound_client
    adapter._client = mock_client

    mock_cb = BaseCallbackHandler()
    parent = {
        "callbacks": [mock_cb],
        "configurable": {"thread_id": "t1", "checkpoint_ns": "diagnose:1"},
        "recursion_limit": 3,
        "run_name": "lyoko",
    }

    result = await adapter.chat(
        prompt="Investigate",
        tools=[MagicMock()],
        trace_name="diagnose-agent",
        tags=["phase:diagnose"],
        max_steps=10,
        parent_config=parent,
    )

    assert result == "done"


@pytest.mark.asyncio
@patch("lyoko.infrastructure.llm.openai.get_langfuse_trace_config")
async def test_nested_call_without_tools_joins_the_parent_trace(mock_get_config):
    adapter = OpenAILLMAdapter(api_key="sk-test")
    client = AsyncMock()
    client.ainvoke.return_value = MagicMock(content="CHAT")
    adapter._client = client

    result = await adapter.chat(
        prompt="hi",
        trace_name="route-llm",
        tags=["phase:route"],
        session_id="ignored",
        parent_config={"callbacks": ["parent_cb"], "configurable": {"thread_id": "t"}},
    )

    assert result == "CHAT"
    mock_get_config.assert_not_called()
    assert client.ainvoke.call_args.kwargs["config"] == {
        "callbacks": ["parent_cb"],
        "run_name": "route-llm",
        "tags": ["phase:route"],
    }


@pytest.mark.asyncio
@patch("lyoko.infrastructure.llm.openai.get_langfuse_trace_config")
async def test_nested_call_works_when_the_parent_has_no_tracer(mock_get_config):
    adapter = OpenAILLMAdapter(api_key="sk-test")
    client = AsyncMock()
    client.ainvoke.return_value = MagicMock(content="CHAT")
    adapter._client = client

    await adapter.chat(prompt="hi", trace_name="route-llm", parent_config={})

    assert client.ainvoke.call_args.kwargs["config"] == {"run_name": "route-llm"}


@pytest.mark.asyncio
async def test_openai_llm_adapter_direct_chat_uses_ainvoke():
    """Verify the tool-less path answers with a single ainvoke call (no streaming)."""
    adapter = OpenAILLMAdapter(api_key="sk-test")
    mock_client = MagicMock()
    response = MagicMock()
    response.content = "Hello world!"
    mock_client.ainvoke = AsyncMock(return_value=response)
    adapter._client = mock_client

    result = await adapter.chat(prompt="Greet")

    assert result == "Hello world!"
    mock_client.ainvoke.assert_awaited_once()


@pytest.mark.asyncio
async def test_openai_llm_adapter_reports_tool_status():
    """Verify on_status is invoked with the factual tool being called during the ReAct loop."""
    tool_call_msg = MagicMock()
    tool_call_msg.tool_calls = [{"name": "k8s_pods_list", "args": {}, "id": "call_1"}]
    final_msg = MagicMock()
    final_msg.tool_calls = []
    final_msg.content = "done"

    mock_bound_client = AsyncMock()
    mock_bound_client.ainvoke.side_effect = [tool_call_msg, final_msg]

    adapter = OpenAILLMAdapter(api_key="sk-test")
    mock_client = MagicMock()
    mock_client.bind_tools.return_value = mock_bound_client
    adapter._client = mock_client

    mock_tool = MagicMock()
    mock_tool.name = "k8s_pods_list"
    mock_tool.ainvoke = AsyncMock(return_value="ok")

    statuses: list[str] = []

    async def on_status(text: str) -> None:
        statuses.append(text)

    result = await adapter.chat(
        prompt="Find pods",
        tools=[mock_tool],
        max_steps=5,
        on_status=on_status,
    )

    assert result == "done"
    assert "🧠 Analyzing request" in statuses
    assert any("Calling <code>k8s_pods_list</code>" in status for status in statuses)


@pytest.mark.asyncio
async def test_openai_llm_adapter_breaks_identical_tool_call_loop():
    """Identical tool calls across consecutive turns break the loop early to prevent runaway cost."""
    tool_call_msg = MagicMock()
    tool_call_msg.tool_calls = [
        {"name": "k8s_pods_list", "args": {"namespace": "default"}, "id": "call_1"}
    ]
    mock_bound_client = AsyncMock()
    mock_bound_client.ainvoke.return_value = tool_call_msg

    adapter = OpenAILLMAdapter(api_key="sk-test")
    mock_client = MagicMock()
    mock_client.bind_tools.return_value = mock_bound_client
    mock_summary_response = MagicMock()
    mock_summary_response.content = "Summary after loop break."
    mock_client.ainvoke = AsyncMock(return_value=mock_summary_response)
    adapter._client = mock_client

    mock_tool = MagicMock()
    mock_tool.name = "k8s_pods_list"
    mock_tool.ainvoke = AsyncMock(return_value="No resources found or empty result.")

    result = await adapter.chat(prompt="Find pods", tools=[mock_tool], max_steps=15)

    # Instead of running 15 steps, it breaks on the 3rd identical attempt (consecutive_repeat_count >= 2)
    assert mock_bound_client.ainvoke.await_count == 3
    mock_client.ainvoke.assert_awaited_once()
    assert result == "Summary after loop break."


@pytest.mark.asyncio
async def test_openai_llm_adapter_truncates_large_tool_output():
    """A single tool result longer than the budget is truncated before entering the context."""
    from lyoko.application.context.buffer import SmartOutputBufferService
    from lyoko.domain.models.scratchpad import SmartBufferConfig
    from lyoko.infrastructure.llm.hermes_runner import HermesHarnessRunner

    tool_call_msg = MagicMock()
    tool_call_msg.tool_calls = [{"name": "k8s_resources_list", "args": {}, "id": "call_1"}]
    final_msg = MagicMock()
    final_msg.tool_calls = []
    final_msg.content = "done"

    mock_bound_client = AsyncMock()
    mock_bound_client.ainvoke.side_effect = [tool_call_msg, final_msg]

    buffer = SmartOutputBufferService(
        config=SmartBufferConfig(max_chars=1000, offload_threshold_chars=100000)
    )
    runner = HermesHarnessRunner(buffer_service=buffer)
    adapter = OpenAILLMAdapter(api_key="sk-test", harness_runner=runner)
    mock_client = MagicMock()
    mock_client.bind_tools.return_value = mock_bound_client
    adapter._client = mock_client

    huge_output = "x" * 50000
    mock_tool = MagicMock()
    mock_tool.name = "k8s_resources_list"
    mock_tool.ainvoke = AsyncMock(return_value=huge_output)

    result = await adapter.chat(prompt="List resources", tools=[mock_tool], max_steps=5)

    assert result == "done"
    second_messages = mock_bound_client.ainvoke.call_args_list[1].args[0]
    tool_messages = [m for m in second_messages if isinstance(m, ToolMessage)]
    assert len(tool_messages) == 1
    assert len(tool_messages[0].content) < len(huge_output)
    assert "truncated" in tool_messages[0].content


@pytest.mark.asyncio
async def test_openai_llm_adapter_bounds_total_tool_calls():
    """The per-run tool-call budget stops execution and forces a final summary."""
    tool_call_msg = MagicMock()
    tool_call_msg.tool_calls = [
        {"name": "k8s_resources_get", "args": {"name": "app-a"}, "id": "c1"},
    ]
    mock_bound_client = AsyncMock()
    mock_bound_client.ainvoke.return_value = tool_call_msg

    adapter = OpenAILLMAdapter(api_key="sk-test")
    mock_client = MagicMock()
    mock_client.bind_tools.return_value = mock_bound_client
    summary = MagicMock()
    summary.content = "Summary after budget."
    mock_client.ainvoke = AsyncMock(return_value=summary)
    adapter._client = mock_client

    mock_tool = MagicMock()
    mock_tool.name = "k8s_resources_get"
    mock_tool.ainvoke = AsyncMock(return_value="ok")

    result = await adapter.chat(prompt="Check all apps", tools=[mock_tool], max_steps=2)

    assert mock_tool.ainvoke.await_count == 2
    assert result == "Summary after budget."


@pytest.mark.asyncio
async def test_openai_llm_adapter_breaks_on_repeated_tool_errors():
    """A tool erroring with different arguments still trips the error guard, not blind retries."""

    def _error_call(regex: str, call_id: str) -> MagicMock:
        msg = MagicMock()
        msg.tool_calls = [
            {
                "name": "grafana_list_prometheus_metric_names",
                "args": {"regex": regex},
                "id": call_id,
            }
        ]
        return msg

    mock_bound_client = AsyncMock()
    mock_bound_client.ainvoke.side_effect = [
        _error_call("temperature", "c1"),
        _error_call("temp", "c2"),
        _error_call("node", "c3"),
    ]

    adapter = OpenAILLMAdapter(api_key="sk-test")
    mock_client = MagicMock()
    mock_client.bind_tools.return_value = mock_bound_client
    summary = MagicMock()
    summary.content = "Summary after repeated errors."
    mock_client.ainvoke = AsyncMock(return_value=summary)
    adapter._client = mock_client

    def _invoke(args):
        return str(
            {
                "status": "error",
                "content": "getting backend: datasource with UID '' not found.",
                "is_error": True,
            }
        )

    mock_tool = MagicMock()
    mock_tool.name = "grafana_list_prometheus_metric_names"
    mock_tool.ainvoke = AsyncMock(side_effect=_invoke)

    result = await adapter.chat(prompt="Node temperature", tools=[mock_tool], max_steps=15)

    assert mock_bound_client.ainvoke.await_count == 2
    assert result == "Summary after repeated errors."


@pytest.mark.asyncio
async def test_openai_llm_adapter_extracts_list_content_blocks():
    """Verify chat extracts clean text when provider returns list of content blocks."""
    adapter = OpenAILLMAdapter(api_key="sk-test", model_name="gpt-4o-mini")
    mock_client = AsyncMock()
    mock_response = MagicMock()
    mock_response.content = [
        {"type": "text", "text": "Node temperatures: 50.85 °C", "id": "msg_abc"}
    ]
    mock_client.ainvoke.return_value = mock_response
    adapter._client = mock_client

    result = await adapter.chat(prompt="Check temperature")
    assert result == "Node temperatures: 50.85 °C"
