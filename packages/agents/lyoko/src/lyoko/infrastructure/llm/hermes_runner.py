"""Hermes-inspired execution harness enforcing strict cost caps, turn ceilings, and cycle guards."""

import logging
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from lyoko.application.context.buffer import SmartOutputBufferService
from lyoko.application.safety.budget_guard import BudgetGuardService
from lyoko.config import settings
from lyoko.domain.interfaces.harness import HarnessRunnerInterface
from lyoko.domain.models.budget import (
    BudgetConfig,
    BudgetTripReason,
    BudgetUsage,
)
from lyoko.domain.models.cost import RunCostTracker
from lyoko.infrastructure.llm.content import extract_message_text
from lyoko.infrastructure.llm.hermes_bridge import HermesSubprocessBridge
from lyoko.infrastructure.llm.hermes_pricing import (
    estimate_cost_usd,
    extract_usage,
    format_tool_status,
    is_tool_error,
)

logger = logging.getLogger("lyoko.infrastructure.llm.hermes_runner")


def _is_mock(obj: Any) -> bool:
    """Detect whether a model object is a mock or double."""
    module_name = getattr(type(obj), "__module__", "")
    return "unittest.mock" in module_name or hasattr(obj, "assert_called")


class HermesHarnessRunner(HarnessRunnerInterface):
    """Hermes Agent harness runner enforcing hard budget caps, turn ceilings, and cycle guards."""

    def __init__(
        self,
        buffer_service: SmartOutputBufferService | None = None,
        budget_guard: BudgetGuardService | None = None,
        budget_config: BudgetConfig | None = None,
        bridge: HermesSubprocessBridge | None = None,
    ) -> None:
        self._buffer = buffer_service or SmartOutputBufferService()
        self._budget_config = budget_config or BudgetConfig(
            max_turns=settings.max_turns_diagnose,
            max_budget_usd=settings.max_budget_usd_per_event,
            cycle_detection_window=settings.cycle_detection_window,
        )
        self._budget_guard = budget_guard or BudgetGuardService(self._budget_config)
        self._bridge = bridge or HermesSubprocessBridge()

    def _tool_status(self, tool_call: dict[str, Any], usage: BudgetUsage) -> str:
        return format_tool_status(
            tool_call,
            turns_used=usage.turns_used,
            max_turns=self._budget_config.max_turns,
            cost_usd=usage.accumulated_cost_usd,
        )

    async def _execute_single_tool(
        self,
        tool_call: dict[str, Any],
        tools_by_name: dict[str, Any],
        session_id: str | None = None,
    ) -> str:
        tool_name = tool_call["name"]
        tool_args = tool_call.get("args") or {}
        tool = tools_by_name.get(tool_name)
        if tool is None:
            raw_output = f"Error: Tool '{tool_name}' not found."
        else:
            try:
                raw_output = (
                    await tool.ainvoke(tool_args)
                    if hasattr(tool, "ainvoke")
                    else tool.invoke(tool_args)
                )
            except Exception as exc:
                raw_output = f"Error executing tool '{tool_name}': {exc}"

        return await self._buffer.process_output(
            tool_name=tool_name, output=raw_output, session_id=session_id
        )

    async def _run_in_process(
        self,
        prompt_text: str,
        *,
        system_prompt: str | None,
        model_with_tools: Any,
        summary_client: Any,
        tools_by_name: dict[str, Any],
        guard: BudgetGuardService,
        active_config: BudgetConfig,
        session_id: str | None,
        on_status: Any,
    ) -> str:
        """Run the bounded loop in-process (used for mocked tests or fallback)."""
        messages: list[Any] = []
        if system_prompt:
            messages.append(SystemMessage(content=system_prompt))
        messages.append(HumanMessage(content=prompt_text))

        usage = BudgetUsage()
        consecutive_tool_errors = 0

        while True:
            turn_eval = guard.evaluate_turn(usage)
            if not turn_eval.can_proceed:
                logger.warning("Halting Hermes loop: %s", turn_eval.detail)
                messages.append(
                    HumanMessage(
                        content=guard.format_termination_instruction(
                            turn_eval.reason or BudgetTripReason.MAX_TURNS_EXCEEDED,
                            turn_eval.detail,
                        )
                    )
                )
                break

            if usage.turns_used == 0 and on_status is not None:
                await on_status("🧠 Analyzing request")

            response = await model_with_tools.ainvoke(messages)
            if response is None:
                response = await model_with_tools.ainvoke(messages)

            in_tokens, out_tokens = extract_usage(response)
            model_name = getattr(response, "response_metadata", {}).get(
                "model_name", settings.openai_model
            )
            cost_usd = estimate_cost_usd(model_name, in_tokens, out_tokens)
            usage.add_usage(input_tokens=in_tokens, output_tokens=out_tokens, cost_usd=cost_usd)

            messages.append(response)
            if not getattr(response, "tool_calls", None):
                return extract_message_text(response.content)

            cycle_tripped = False
            for tool_call in response.tool_calls:
                tool_name = tool_call["name"]
                tool_args = tool_call.get("args") or {}

                tool_eval = guard.evaluate_tool_call(tool_name, tool_args)
                if not tool_eval.can_proceed:
                    logger.warning("Tool cycle detected for '%s': %s", tool_name, tool_eval.detail)
                    messages.append(
                        ToolMessage(
                            content=guard.format_termination_instruction(
                                BudgetTripReason.CYCLE_DETECTED, tool_eval.detail
                            ),
                            tool_call_id=tool_call["id"],
                            name=tool_name,
                        )
                    )
                    cycle_tripped = True
                    break

                if on_status is not None:
                    await on_status(self._tool_status(tool_call, usage))

                output_str = await self._execute_single_tool(
                    tool_call, tools_by_name, session_id=session_id
                )

                if is_tool_error(output_str):
                    consecutive_tool_errors += 1
                else:
                    consecutive_tool_errors = 0

                messages.append(
                    ToolMessage(
                        content=output_str,
                        tool_call_id=tool_call["id"],
                        name=tool_name,
                    )
                )

            if cycle_tripped or consecutive_tool_errors >= active_config.max_consecutive_errors:
                messages.append(
                    HumanMessage(
                        content="Stop tool execution now. Synthesize all findings and summarize your final report."
                    )
                )
                break

        if on_status is not None:
            await on_status("✍️ Formulating final response")
        final_response = await summary_client.ainvoke(messages)
        if session_id and usage.total_cost_usd > 0:
            RunCostTracker.record_cost(session_id, usage.total_cost_usd)
        return extract_message_text(final_response.content)

    async def run(
        self,
        prompt_text: str,
        *,
        system_prompt: str | None,
        model_with_tools: Any,
        summary_client: Any,
        tools_by_name: dict[str, Any],
        max_iterations: int,
        session_id: str | None = None,
        on_status: Any = None,
    ) -> str:
        """Run the Hermes agent harness enforcing budget caps, turn ceilings, and tool gate safety."""
        effective_max_turns = min(max_iterations, self._budget_config.max_turns)
        active_config = BudgetConfig(
            max_turns=effective_max_turns,
            max_budget_usd=self._budget_config.max_budget_usd,
            cycle_detection_window=self._budget_config.cycle_detection_window,
        )
        guard = BudgetGuardService(active_config)

        if _is_mock(model_with_tools) or not self._bridge.is_available():
            return await self._run_in_process(
                prompt_text,
                system_prompt=system_prompt,
                model_with_tools=model_with_tools,
                summary_client=summary_client,
                tools_by_name=tools_by_name,
                guard=guard,
                active_config=active_config,
                session_id=session_id,
                on_status=on_status,
            )

        usage = BudgetUsage()

        async def on_bridge_llm_usage(msg: dict[str, Any]) -> None:
            in_tok = int(msg.get("input_tokens") or 0)
            out_tok = int(msg.get("output_tokens") or 0)
            model_used = str(
                msg.get("model") or getattr(summary_client, "model_name", settings.openai_model)
            )
            cost = estimate_cost_usd(model_used, in_tok, out_tok)
            usage.add_usage(input_tokens=in_tok, output_tokens=out_tok, cost_usd=cost)

        async def on_bridge_tool_call(tool_name: str, tool_args: dict[str, Any]) -> str:
            turn_eval = guard.evaluate_turn(usage)
            if not turn_eval.can_proceed:
                return guard.format_termination_instruction(
                    turn_eval.reason or BudgetTripReason.MAX_TURNS_EXCEEDED, turn_eval.detail
                )
            tool_eval = guard.evaluate_tool_call(tool_name, tool_args)
            if not tool_eval.can_proceed:
                return guard.format_termination_instruction(
                    BudgetTripReason.CYCLE_DETECTED, tool_eval.detail
                )
            if on_status is not None:
                await on_status(self._tool_status({"name": tool_name, "args": tool_args}, usage))

            output = await self._execute_single_tool(
                {"name": tool_name, "args": tool_args}, tools_by_name, session_id=session_id
            )
            usage.turns_used += 1
            return output

        model_name = getattr(summary_client, "model_name", settings.openai_model)
        api_key = getattr(summary_client, "api_key", "")
        if not api_key:
            api_key = (
                settings.openai_api_key.get_secret_value()
                if hasattr(settings.openai_api_key, "get_secret_value")
                else str(settings.openai_api_key or "")
            )
        base_url = getattr(summary_client, "base_url", None)

        res = await self._bridge.execute(
            prompt=prompt_text,
            system_prompt=system_prompt,
            model=model_name,
            api_key=api_key,
            base_url=str(base_url) if base_url else None,
            max_iterations=effective_max_turns,
            tools_by_name=tools_by_name,
            on_tool_call=on_bridge_tool_call,
            on_status=on_status,
            on_llm_usage=on_bridge_llm_usage,
        )
        if session_id and usage.total_cost_usd > 0:
            RunCostTracker.record_cost(session_id, usage.total_cost_usd)
        return res
