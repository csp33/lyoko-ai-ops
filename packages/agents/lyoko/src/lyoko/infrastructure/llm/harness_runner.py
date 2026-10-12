"""Class-based ReAct agent harness runner integrating output buffering and guardrails."""

import json
import logging
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from lyoko.application.context.buffer import SmartOutputBufferService
from lyoko.config import settings
from lyoko.domain.interfaces.harness import HarnessRunnerInterface
from lyoko.infrastructure.llm.content import extract_message_text

logger = logging.getLogger("lyoko.infrastructure.llm.harness_runner")

_ERROR_MARKERS = (
    "'is_error': true",
    '"is_error": true',
    '"is_error":true',
    "error executing tool",
    "not found on any upstream",
    "is forbidden:",
    "permission denied",
)


class ReActHarnessRunner(HarnessRunnerInterface):
    """Bounded, error-aware ReAct execution harness implementing HarnessRunnerInterface."""

    def __init__(
        self,
        buffer_service: SmartOutputBufferService | None = None,
        max_consecutive_errors: int = 3,
    ) -> None:
        self._custom_buffer = buffer_service is not None
        self._buffer = buffer_service or SmartOutputBufferService()
        self._max_consecutive_errors = max_consecutive_errors

    def _is_tool_error(self, output: str) -> bool:
        lowered = output.lower()
        return any(marker in lowered for marker in _ERROR_MARKERS)

    def _tool_status(self, tool_call: dict[str, Any]) -> str:
        name = str(tool_call.get("name") or "tool")
        args = tool_call.get("args") or {}
        if name.startswith("ask_") and name.endswith("_specialist"):
            return f"🧩 Consulting the {name[4:-11]} specialist"
        if name == "gateway_call_tool":
            return f"🛰️ Calling <code>{args.get('tool_name', 'tool')}</code>"
        if name == "gateway_get_domain_tools":
            return f"🔍 Discovering {args.get('domain', 'domain')} tools"
        if name == "gateway_get_tool_schema":
            return f"🔍 Reading the <code>{args.get('tool_name', 'tool')}</code> schema"
        return f"🛰️ Calling <code>{name}</code>"

    def _detect_cycle(self, history: list[tuple[tuple[str, str], ...]]) -> int | None:
        n = len(history)
        if n >= 3 and history[-1] == history[-2] == history[-3]:
            return 1
        if n >= 4 and history[-4:-2] == history[-2:]:
            return 2
        if n >= 6 and history[-6:-3] == history[-3:]:
            return 3
        return None

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

        if (
            not self._custom_buffer
            and self._buffer.config.max_chars != settings.max_tool_output_chars
        ):
            from lyoko.domain.models.scratchpad import SmartBufferConfig

            self._buffer._config = SmartBufferConfig(
                max_chars=settings.max_tool_output_chars,
                offload_threshold_chars=self._buffer.config.offload_threshold_chars,
                head_ratio=self._buffer.config.head_ratio,
            )

        return await self._buffer.process_output(
            tool_name=tool_name,
            output=raw_output,
            session_id=session_id,
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
        """Run the bounded ReAct loop until the model completes or a limit trips."""
        messages: list[Any] = []
        if system_prompt:
            messages.append(SystemMessage(content=system_prompt))
        messages.append(HumanMessage(content=prompt_text))

        tool_call_budget = settings.max_tool_calls_per_run
        executed_tool_calls = 0
        call_history: list[tuple[tuple[str, str], ...]] = []
        consecutive_tool_errors = 0
        last_error_fingerprint: str | None = None
        repeated_error_count = 0

        for step in range(max_iterations):
            if step == 0 and on_status is not None:
                await on_status("🧠 Analyzing the request")
            response = await model_with_tools.ainvoke(messages)
            if response is None:
                response = await model_with_tools.ainvoke(messages)

            messages.append(response)
            if not response.tool_calls:
                return extract_message_text(response.content)

            current_sig = tuple(
                (tc.get("name", ""), json.dumps(tc.get("args") or {}, sort_keys=True))
                for tc in response.tool_calls
            )

            history_with_current = [*call_history, current_sig]
            cycle_len = self._detect_cycle(history_with_current)
            call_occurrences = call_history.count(current_sig)

            if cycle_len is not None or call_occurrences >= 2:
                logger.warning("Breaking ReAct loop due to cycle or repeat detection.")
                for tc in response.tool_calls:
                    messages.append(
                        ToolMessage(
                            content=(
                                "[System Note: Repeated tool call cycle detected. Execution stopped "
                                "to prevent infinite loop. Summarize findings now without further calls.]"
                            ),
                            tool_call_id=tc["id"],
                            name=tc.get("name", "tool"),
                        )
                    )
                break

            call_history.append(current_sig)
            budget_exceeded = False

            for tool_call in response.tool_calls:
                tool_name = tool_call["name"]
                if executed_tool_calls >= tool_call_budget:
                    budget_exceeded = True
                    messages.append(
                        ToolMessage(
                            content=(
                                "[System Note: Tool-call budget reached for this run. "
                                "This call was not executed; summarize your findings now "
                                "without further tool calls.]"
                            ),
                            tool_call_id=tool_call["id"],
                            name=tool_name,
                        )
                    )
                    continue

                executed_tool_calls += 1
                if on_status is not None:
                    await on_status(self._tool_status(tool_call))

                output_str = await self._execute_single_tool(
                    tool_call, tools_by_name, session_id=session_id
                )

                if call_occurrences >= 1:
                    output_str += (
                        "\n\n[System Note: This tool was called with the exact same "
                        "arguments earlier in this run. If no resources were returned or "
                        "information was already gathered, conclude that they do not exist "
                        "or synthesize your findings instead of repeating identical queries or oscillating.]"
                    )

                if self._is_tool_error(output_str):
                    consecutive_tool_errors += 1
                    fingerprint = f"{tool_name}|{output_str[:200]}"
                    if fingerprint == last_error_fingerprint:
                        repeated_error_count += 1
                    else:
                        repeated_error_count = 0
                    last_error_fingerprint = fingerprint
                    if repeated_error_count >= 1 or consecutive_tool_errors >= 2:
                        output_str += (
                            "\n\n[System Note: This tool keeps failing. Do not retry more "
                            "variants. Read its schema with gateway_get_tool_schema and supply "
                            "the missing or invalid argument, or switch to a different tool. "
                            "If the failure is a permissions error, report it instead of "
                            "retrying.]"
                        )
                else:
                    consecutive_tool_errors = 0
                    last_error_fingerprint = None
                    repeated_error_count = 0

                messages.append(
                    ToolMessage(
                        content=output_str,
                        tool_call_id=tool_call["id"],
                        name=tool_name,
                    )
                )

            if budget_exceeded or consecutive_tool_errors >= self._max_consecutive_errors:
                break

        messages.append(
            HumanMessage(
                content="You have reached the maximum allowed steps. Please summarize your findings, actions taken, and current status based on the information gathered so far without making further tool calls."
            )
        )
        if on_status is not None:
            await on_status("✍️ Writing the answer")
        final_response = await summary_client.ainvoke(messages)
        return extract_message_text(final_response.content)
