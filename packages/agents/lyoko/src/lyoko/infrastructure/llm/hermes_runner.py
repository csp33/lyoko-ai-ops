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
from lyoko.infrastructure.llm.content import extract_message_text

logger = logging.getLogger("lyoko.infrastructure.llm.hermes_runner")

_ERROR_MARKERS = (
    "'is_error': true",
    '"is_error": true',
    '"is_error":true',
    "error executing tool",
    "not found on any upstream",
    "is forbidden:",
    "permission denied",
)


def _estimate_cost_usd(model_name: str, input_tokens: int, output_tokens: int) -> float:
    """Estimate USD cost based on known OpenAI pricing tiers."""
    lowered = model_name.lower()
    if "mini" in lowered:
        return (input_tokens * 0.15 + output_tokens * 0.60) / 1_000_000
    if "4o" in lowered or "4.1" in lowered:
        return (input_tokens * 2.50 + output_tokens * 10.00) / 1_000_000
    return (input_tokens * 1.50 + output_tokens * 6.00) / 1_000_000


class HermesHarnessRunner(HarnessRunnerInterface):
    """Hermes Agent harness runner enforcing hard budget caps, turn ceilings, and cycle guards."""

    def __init__(
        self,
        buffer_service: SmartOutputBufferService | None = None,
        budget_guard: BudgetGuardService | None = None,
        budget_config: BudgetConfig | None = None,
    ) -> None:
        self._buffer = buffer_service or SmartOutputBufferService()
        self._budget_config = budget_config or BudgetConfig(
            max_turns=settings.max_turns_diagnose,
            max_budget_usd=settings.max_budget_usd_per_event,
            cycle_detection_window=settings.cycle_detection_window,
        )
        self._budget_guard = budget_guard or BudgetGuardService(self._budget_config)

    def _is_tool_error(self, output: str) -> bool:
        lowered = output.lower()
        return any(marker in lowered for marker in _ERROR_MARKERS)

    def _tool_status(self, tool_call: dict[str, Any], usage: BudgetUsage) -> str:
        name = str(tool_call.get("name") or "tool")
        args = tool_call.get("args") or {}
        step_info = f"(Step {usage.turns_used + 1}/{self._budget_config.max_turns} • ${usage.accumulated_cost_usd:.3f})"
        if name.startswith("ask_") and name.endswith("_specialist"):
            return f"🧩 Consulting {name[4:-11]} specialist {step_info}"
        if name == "gateway_call_tool":
            return f"🛰️ Calling <code>{args.get('tool_name', 'tool')}</code> {step_info}"
        if name == "gateway_get_tool_schema":
            return f"🔍 Reading <code>{args.get('tool_name', 'tool')}</code> schema {step_info}"
        return f"🛰️ Calling <code>{name}</code> {step_info}"

    async def _execute_single_tool(
        self,
        tool_call: dict[str, Any],
        tools_by_name: dict[str, Any],
        session_id: str | None = None,
    ) -> str:
        tool_name = tool_call["name"]
        tool_args = tool_call["args"]
        tool = tools_by_name.get(tool_name)
        if tool is None:
            raw_output = f"Error: Tool '{tool_name}' not found."
        else:
            try:
                if hasattr(tool, "ainvoke"):
                    raw_output = await tool.ainvoke(tool_args)
                else:
                    raw_output = tool.invoke(tool_args)
            except Exception as exc:
                raw_output = f"Error executing tool '{tool_name}': {exc}"

        return await self._buffer.process_output(
            tool_name=tool_name,
            output=raw_output,
            session_id=session_id,
        )

    def _extract_usage(self, response: Any) -> tuple[int, int]:
        """Extract input and output tokens from model response metadata."""
        usage_meta = getattr(response, "usage_metadata", None) or {}
        if usage_meta:
            return (
                int(usage_meta.get("input_tokens", 0)),
                int(usage_meta.get("output_tokens", 0)),
            )
        resp_meta = getattr(response, "response_metadata", None) or {}
        token_usage = resp_meta.get("token_usage", {})
        return (
            int(token_usage.get("prompt_tokens", 0)),
            int(token_usage.get("completion_tokens", 0)),
        )

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
        """Run the Hermes-style bounded loop until resolution or budget trip."""
        messages: list[Any] = []
        if system_prompt:
            messages.append(SystemMessage(content=system_prompt))
        messages.append(HumanMessage(content=prompt_text))

        effective_max_turns = min(max_iterations, self._budget_config.max_turns)
        active_config = BudgetConfig(
            max_turns=effective_max_turns,
            max_budget_usd=self._budget_config.max_budget_usd,
            cycle_detection_window=self._budget_config.cycle_detection_window,
        )
        guard = BudgetGuardService(active_config)
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

            in_tokens, out_tokens = self._extract_usage(response)
            model_name = getattr(response, "response_metadata", {}).get(
                "model_name", settings.openai_model
            )
            cost_usd = _estimate_cost_usd(model_name, in_tokens, out_tokens)
            usage.add_usage(input_tokens=in_tokens, output_tokens=out_tokens, cost_usd=cost_usd)

            messages.append(response)
            if not response.tool_calls:
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

                if self._is_tool_error(output_str):
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
        return extract_message_text(final_response.content)
