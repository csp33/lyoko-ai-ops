"""Domain model defining the shared graph state for LYOKO."""

from __future__ import annotations

from typing import Any, TypedDict

from lyoko.domain.models.incident import IncidentStatus

EVENT_ALERT = "alert"
EVENT_MESSAGE = "message"


class LyokoState(TypedDict, total=False):
    # What happened. ``event_type`` is "alert" (the default) or "message".
    event_type: str
    event_id: str
    """Short id of this run. It prefixes approval ids, which end up in Telegram callback data."""
    session_id: str
    """Observability session the run belongs to (a chat session, or the incident)."""
    trace_id: str | None
    """Langfuse trace ID for this run, used for linking and cost metrics."""
    chat_id: str
    message_thread_id: str | None
    """Telegram forum topic / thread of the originating message, for threaded replies."""
    text: str
    """The operator's message, for ``event_type == "message"``."""
    alert_name: str
    labels: dict[str, str]
    annotations: dict[str, str]

    # Routing and chat branch
    route: str
    reply: str
    """The text to send back to the operator, set by ``chat`` and, for messages, by ``notify``."""

    # Incident branch
    status: IncidentStatus | str
    root_cause: str
    plan: str
    action_taken: str
    actions: list[dict[str, Any]]
    verification: str
    is_resolved: bool
    requires_escalation: bool
    correlated_alerts: list[dict[str, Any]]
    progress_message_id: str | None
    progress_chat_id: str | None

    # Triage dispatch
    triage_dispatched: bool
    triage_handler: str | None
    coordinator_next: str | None


def is_message(state: dict[str, Any] | LyokoState) -> bool:
    """Return True if the state represents an operator chat message event."""
    return state.get("event_type", EVENT_ALERT) == EVENT_MESSAGE
