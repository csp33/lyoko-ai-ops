"""Application service enforcing budget caps, turn ceilings, and cycle detection."""

import json
from typing import Any

from lyoko.domain.models.budget import (
    BudgetConfig,
    BudgetEvaluation,
    BudgetTripReason,
    BudgetUsage,
)


class BudgetGuardService:
    """Enforces execution turn limits, dollar cost budgets, and tool cycle prevention."""

    def __init__(self, config: BudgetConfig | None = None) -> None:
        self.config = config or BudgetConfig()
        self._call_history: list[tuple[str, str]] = []

    def evaluate_turn(self, usage: BudgetUsage) -> BudgetEvaluation:
        """Evaluate if the next reasoning turn can proceed under turn and cost bounds."""
        if usage.turns_used >= self.config.max_turns:
            return BudgetEvaluation(
                can_proceed=False,
                reason=BudgetTripReason.MAX_TURNS_EXCEEDED,
                detail=(
                    f"Turn ceiling reached ({usage.turns_used}/{self.config.max_turns}). "
                    "Halting further tool invocations."
                ),
            )

        if usage.accumulated_cost_usd >= self.config.max_budget_usd:
            return BudgetEvaluation(
                can_proceed=False,
                reason=BudgetTripReason.MAX_BUDGET_EXCEEDED,
                detail=(
                    f"Hard budget cap reached (${usage.accumulated_cost_usd:.4f} >= "
                    f"${self.config.max_budget_usd:.2f}). Halting further execution."
                ),
            )

        return BudgetEvaluation(can_proceed=True)

    def evaluate_tool_call(self, tool_name: str, arguments: dict[str, Any]) -> BudgetEvaluation:
        """Detect repetitive identical tool invocations in the cycle detection window."""
        try:
            serialized_args = json.dumps(arguments, sort_keys=True)
        except (TypeError, ValueError):
            serialized_args = str(arguments)

        call_signature = (tool_name, serialized_args)

        # Check if the same call signature was performed recently
        window = self._call_history[-self.config.cycle_detection_window :]
        if len(window) >= self.config.cycle_detection_window and all(
            item == call_signature for item in window
        ):
            return BudgetEvaluation(
                can_proceed=False,
                reason=BudgetTripReason.CYCLE_DETECTED,
                detail=(
                    f"Cycle detected: '{tool_name}' was invoked with identical arguments "
                    f"{len(window)} times consecutively."
                ),
            )

        self._call_history.append(call_signature)
        return BudgetEvaluation(can_proceed=True)

    def format_termination_instruction(self, reason: BudgetTripReason, detail: str) -> str:
        """Generate guidance for the model to summarize existing findings gracefully."""
        if reason == BudgetTripReason.MAX_TURNS_EXCEEDED:
            return (
                f"[SYSTEM NOTICE: {detail}]\n"
                "You have reached your maximum execution turns. Do NOT call any more tools. "
                "Synthesize all evidence gathered so far, explain root causes found, and "
                "provide a clear final summary."
            )
        if reason == BudgetTripReason.MAX_BUDGET_EXCEEDED:
            return (
                f"[SYSTEM NOTICE: {detail}]\n"
                "The operational spend ceiling has been reached. Stop all tool calls immediately "
                "and report your final status and budget warning."
            )
        return (
            f"[SYSTEM NOTICE: {detail}]\n"
            "Do not retry this identical tool call. Adapt your strategy or summarize findings."
        )
