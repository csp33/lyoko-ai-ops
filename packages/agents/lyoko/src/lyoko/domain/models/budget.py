"""Domain models for agent harness budgeting, turn ceilings, and cost tracking."""

from dataclasses import dataclass
from enum import StrEnum


class BudgetTripReason(StrEnum):
    """Reason why an agent harness budget or ceiling tripped."""

    MAX_TURNS_EXCEEDED = "max_turns_exceeded"
    MAX_BUDGET_EXCEEDED = "max_budget_exceeded"
    CYCLE_DETECTED = "cycle_detected"


@dataclass(frozen=True)
class BudgetConfig:
    """Configuration for execution bounds and cost ceilings."""

    max_turns: int = 4
    max_budget_usd: float = 0.10
    cycle_detection_window: int = 2
    max_consecutive_errors: int = 2


@dataclass
class BudgetUsage:
    """Tracks cumulative usage and expenditure across harness turns."""

    turns_used: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    accumulated_cost_usd: float = 0.0

    def add_usage(
        self,
        *,
        input_tokens: int,
        output_tokens: int,
        cost_usd: float,
    ) -> None:
        """Accumulate token counts and estimated cost."""
        self.turns_used += 1
        self.input_tokens += input_tokens
        self.output_tokens += output_tokens
        self.accumulated_cost_usd += cost_usd


@dataclass(frozen=True)
class BudgetEvaluation:
    """Result of evaluating whether an execution step can proceed."""

    can_proceed: bool
    reason: BudgetTripReason | None = None
    detail: str = ""
