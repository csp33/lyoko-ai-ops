"""Base models and protocols for deterministic triage handlers."""

from dataclasses import dataclass, field
from typing import Any, Protocol

from langchain_core.runnables import RunnableConfig


@dataclass(frozen=True)
class TriageResult:
    """Outcome of a deterministic triage handler."""

    handled: bool
    root_cause: str = ""
    plan: str = ""
    action_taken: str = ""
    actions: list[dict[str, Any]] = field(default_factory=list)
    verification: str = ""
    is_resolved: bool = False
    requires_escalation: bool = False

    def to_state_patch(self) -> dict[str, Any]:
        """Convert triage outcome to LangGraph state patch."""
        if not self.handled:
            return {"triage": "diagnose"}
        return {
            "triage": "handled",
            "root_cause": self.root_cause,
            "plan": self.plan,
            "action_taken": self.action_taken,
            "actions": self.actions,
            "verification": self.verification,
            "is_resolved": self.is_resolved,
            "requires_escalation": self.requires_escalation,
        }


class TriageHandler(Protocol):
    """Protocol implemented by deterministic triage handlers."""

    def can_handle(self, state: dict[str, Any]) -> bool:
        """Check if this handler can deterministically handle the alert or message."""
        ...

    async def execute(self, state: dict[str, Any], config: RunnableConfig) -> TriageResult | None:
        """Execute deterministic triage and return result, or None to fall through."""
        ...
