"""Routing parser of operator messages: a plain conversation, or an incident to work through."""

import re

from lyoko.domain.models.routing import Route

_ROUTE_RE = re.compile(r"\b(CHAT|INCIDENT)\b", re.IGNORECASE)


class RouteClassifier:
    """Parses the routing decision from an LLM response."""

    @staticmethod
    def parse_route(answer: str) -> Route:
        """Read the router's one-word answer. Anything unclear falls back to ``Route.CHAT``.

        Chat is the safe default: it answers and acts through the same approval gate, whereas
        the incident flow would start an investigation nobody asked for.
        """
        match = _ROUTE_RE.search(answer or "")
        if match and match.group(1).upper() == "INCIDENT":
            return Route.INCIDENT
        return Route.CHAT
