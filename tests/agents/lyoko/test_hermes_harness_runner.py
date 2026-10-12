"""Unit tests for HermesHarnessRunner enforcing turn and budget limits."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from lyoko.domain.models.budget import BudgetConfig
from lyoko.infrastructure.llm.hermes_runner import HermesHarnessRunner


@pytest.mark.asyncio
async def test_hermes_runner_halts_on_max_turns():
    budget_config = BudgetConfig(max_turns=2, max_budget_usd=1.0)
    runner = HermesHarnessRunner(budget_config=budget_config)

    call1 = MagicMock()
    call1.tool_calls = [{"name": "k8s_get", "args": {"pod": "p1"}, "id": "c1"}]
    call1.content = ""
    call1.usage_metadata = {"input_tokens": 100, "output_tokens": 50}

    call2 = MagicMock()
    call2.tool_calls = [{"name": "k8s_get", "args": {"pod": "p2"}, "id": "c2"}]
    call2.content = ""
    call2.usage_metadata = {"input_tokens": 100, "output_tokens": 50}

    summary_msg = MagicMock()
    summary_msg.content = "Summary: maximum turns reached."
    summary_msg.tool_calls = []

    model_with_tools = AsyncMock()
    model_with_tools.ainvoke.side_effect = [call1, call2]

    summary_client = AsyncMock()
    summary_client.ainvoke = AsyncMock(return_value=summary_msg)

    tool_mock = MagicMock()
    tool_mock.name = "k8s_get"
    tool_mock.ainvoke = AsyncMock(return_value="pod_info")

    result = await runner.run(
        prompt_text="Investigate issue",
        system_prompt="You are Lyoko",
        model_with_tools=model_with_tools,
        summary_client=summary_client,
        tools_by_name={"k8s_get": tool_mock},
        max_iterations=10,
    )

    assert result == "Summary: maximum turns reached."
    # Model should have been called at most 2 times before turn limit kicked in
    assert model_with_tools.ainvoke.call_count == 2
    assert summary_client.ainvoke.call_count == 1


@pytest.mark.asyncio
async def test_hermes_runner_halts_on_hard_cost_cap():
    # Budget cap set to $0.001 (very small to trigger immediately)
    budget_config = BudgetConfig(max_turns=10, max_budget_usd=0.001)
    runner = HermesHarnessRunner(budget_config=budget_config)

    # 10,000 tokens on gpt-4o (~$0.025, which exceeds $0.001)
    call1 = MagicMock()
    call1.tool_calls = [{"name": "k8s_get", "args": {"pod": "p1"}, "id": "c1"}]
    call1.content = ""
    call1.response_metadata = {"model_name": "gpt-4o"}
    call1.usage_metadata = {"input_tokens": 10000, "output_tokens": 500}

    summary_msg = MagicMock()
    summary_msg.content = "Summary: hard budget cap reached."
    summary_msg.tool_calls = []

    model_with_tools = AsyncMock()
    model_with_tools.ainvoke.side_effect = [call1]

    summary_client = AsyncMock()
    summary_client.ainvoke = AsyncMock(return_value=summary_msg)

    tool_mock = MagicMock()
    tool_mock.name = "k8s_get"
    tool_mock.ainvoke = AsyncMock(return_value="pod_info")

    result = await runner.run(
        prompt_text="Investigate issue",
        system_prompt="You are Lyoko",
        model_with_tools=model_with_tools,
        summary_client=summary_client,
        tools_by_name={"k8s_get": tool_mock},
        max_iterations=10,
    )

    assert result == "Summary: hard budget cap reached."
    # Loop should have broken after call1 due to cost exceeding $0.001
    assert model_with_tools.ainvoke.call_count == 1
    assert summary_client.ainvoke.call_count == 1


@pytest.mark.asyncio
async def test_hermes_runner_breaks_on_cycle_detection():
    budget_config = BudgetConfig(max_turns=10, max_budget_usd=1.0, cycle_detection_window=2)
    runner = HermesHarnessRunner(budget_config=budget_config)

    def make_call(call_id: str):
        msg = MagicMock()
        msg.tool_calls = [{"name": "k8s_get", "args": {"pod": "same_pod"}, "id": call_id}]
        msg.content = ""
        msg.usage_metadata = {"input_tokens": 50, "output_tokens": 10}
        return msg

    summary_msg = MagicMock()
    summary_msg.content = "Summary after cycle detected."
    summary_msg.tool_calls = []

    model_with_tools = AsyncMock()
    model_with_tools.ainvoke.side_effect = [
        make_call("c1"),
        make_call("c2"),
        make_call("c3"),
    ]

    summary_client = AsyncMock()
    summary_client.ainvoke = AsyncMock(return_value=summary_msg)

    tool_mock = MagicMock()
    tool_mock.name = "k8s_get"
    tool_mock.ainvoke = AsyncMock(return_value="same_result")

    result = await runner.run(
        prompt_text="Investigate pod",
        system_prompt="You are Lyoko",
        model_with_tools=model_with_tools,
        summary_client=summary_client,
        tools_by_name={"k8s_get": tool_mock},
        max_iterations=10,
    )

    assert result == "Summary after cycle detected."


@pytest.mark.asyncio
async def test_hermes_runner_emits_status_heartbeat():
    budget_config = BudgetConfig(max_turns=4, max_budget_usd=1.0)
    runner = HermesHarnessRunner(budget_config=budget_config)

    call1 = MagicMock()
    call1.tool_calls = [{"name": "k8s_get", "args": {"pod": "p1"}, "id": "c1"}]
    call1.content = ""
    call1.usage_metadata = {"input_tokens": 100, "output_tokens": 50}

    final_msg = MagicMock()
    final_msg.tool_calls = []
    final_msg.content = "Done."

    model_with_tools = AsyncMock()
    model_with_tools.ainvoke.side_effect = [call1, final_msg]

    summary_client = AsyncMock()

    tool_mock = MagicMock()
    tool_mock.name = "k8s_get"
    tool_mock.ainvoke = AsyncMock(return_value="pod_info")

    statuses = []

    async def on_status(msg: str):
        statuses.append(msg)

    result = await runner.run(
        prompt_text="Check",
        system_prompt=None,
        model_with_tools=model_with_tools,
        summary_client=summary_client,
        tools_by_name={"k8s_get": tool_mock},
        max_iterations=5,
        on_status=on_status,
    )

    assert result == "Done."
    assert any("Analyzing request" in s for s in statuses)
    assert any("Calling <code>k8s_get</code>" in s for s in statuses)


@pytest.mark.asyncio
async def test_hermes_runner_delegates_to_subprocess_bridge():
    bridge_mock = MagicMock()
    bridge_mock.is_available.return_value = True
    bridge_mock.execute = AsyncMock(return_value="Subprocess result from Hermes Agent")

    runner = HermesHarnessRunner(bridge=bridge_mock)

    real_summary_client = type(
        "RealClient", (), {"model_name": "gpt-4o-mini", "api_key": "sk-real"}
    )()
    # non-mock model object
    real_model = type("RealModel", (), {})()

    tool_mock = MagicMock()
    tool_mock.name = "k8s_get"

    result = await runner.run(
        prompt_text="Investigate issue",
        system_prompt="You are Lyoko",
        model_with_tools=real_model,
        summary_client=real_summary_client,
        tools_by_name={"k8s_get": tool_mock},
        max_iterations=4,
        session_id="telegram-12345",
        trace_id="trace-999",
    )

    assert result == "Subprocess result from Hermes Agent"
    assert bridge_mock.execute.call_count == 1
    call_kwargs = bridge_mock.execute.call_args.kwargs
    assert call_kwargs["prompt"] == "Investigate issue"
    assert call_kwargs["model"] == "gpt-4o-mini"
    assert call_kwargs["max_iterations"] == 4
    assert call_kwargs["session_id"] == "telegram-12345"
    assert call_kwargs["trace_id"] == "trace-999"
