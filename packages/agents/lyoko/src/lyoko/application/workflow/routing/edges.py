"""Conditional edge routing predicates for the LYOKO StateGraph."""

from typing import Any

from lyoko.domain.models.incident import CoordinatorNext, SpecialistDomain
from lyoko.domain.models.routing import Route
from lyoko.domain.models.state import is_message

SPECIALIST_DOMAINS = frozenset(SpecialistDomain)


class WorkflowRouteSelector:
    """Selects state transitions and conditional branches in the LYOKO workflow graph."""

    @staticmethod
    def choose_branch(state: dict[str, Any]) -> str:
        """Route: 'chat' takes the interactive branch, 'diagnose' takes the triage/incident branch."""
        return "chat" if state.get("route") == Route.CHAT.value else "diagnose"

    @staticmethod
    def choose_coordinator_next(state: dict[str, Any]) -> CoordinatorNext:
        """Determine the next node after coordinator execution.

        - If a specialist delegation is pending, route to that specialist node.
        - If event is chat (message), route to END.
        - If incident, route to remediate.
        """
        pending = state.get("pending_delegation")
        if pending and isinstance(pending, dict):
            domain = pending.get("domain")
            if domain in SPECIALIST_DOMAINS:
                return CoordinatorNext(domain)

        # Chat branch resolution goes directly to END (reply is already formatted)
        if is_message(state) and state.get("route") != "incident":
            return CoordinatorNext.CHAT_END

        return CoordinatorNext.REMEDIATE
