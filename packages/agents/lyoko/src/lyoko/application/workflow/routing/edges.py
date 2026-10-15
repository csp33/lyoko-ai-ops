"""Conditional edge routing predicates for the LYOKO workflow."""

from typing import Any

from lyoko.domain.models.incident import CoordinatorNext
from lyoko.domain.models.routing import Route
from lyoko.domain.models.state import is_message


class WorkflowRouteSelector:
    """Selects state transitions and conditional branches in the LYOKO workflow."""

    @staticmethod
    def choose_branch(state: dict[str, Any]) -> str:
        """Route: 'chat' takes the interactive branch, 'diagnose' takes the triage/incident branch."""
        return "chat" if state.get("route") == Route.CHAT.value else "diagnose"

    @staticmethod
    def choose_coordinator_next(state: dict[str, Any]) -> CoordinatorNext:
        """Determine the next step after coordinator execution."""
        # Chat branch resolution completes after answering
        if is_message(state) and state.get("route") != "incident":
            return CoordinatorNext.CHAT_END

        return CoordinatorNext.NOTIFY
