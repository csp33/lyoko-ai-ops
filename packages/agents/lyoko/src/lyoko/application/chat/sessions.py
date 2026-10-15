"""Observability session management for chat conversations.

Langfuse sessions are plain groupings of traces sharing an ID and cannot be closed
explicitly. To keep sessions meaningful (one conversation topic or one incident per
session), a chat's session ID is derived as follows:

1. Replying to a message linked to a session (e.g. an incident alert or approval request)
   continues that session, so follow-up questions about an alert stay attached to it.
2. ``start_new`` (the ``/new`` command) explicitly begins a fresh session.
3. Otherwise the current session is reused until the chat has been idle for longer than
   the idle timeout, after which a new session is started.
"""

from collections import OrderedDict
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

DEFAULT_SESSION_IDLE_TIMEOUT_SECONDS = 900
MAX_LINKED_MESSAGES = 1000


class ChatSessionTracker:
    """Track the active observability session for each chat."""

    def __init__(
        self,
        idle_timeout_seconds: float = DEFAULT_SESSION_IDLE_TIMEOUT_SECONDS,
        clock: Callable[[], datetime] | None = None,
        max_linked_messages: int = MAX_LINKED_MESSAGES,
    ) -> None:
        self._idle_timeout = timedelta(seconds=idle_timeout_seconds)
        self._clock = clock or (lambda: datetime.now(UTC))
        self._max_linked_messages = max_linked_messages
        # chat_id -> (session_id, last_activity)
        self._sessions: dict[str, tuple[str, datetime]] = {}
        # (chat_id, message_id) -> session_id, oldest entries evicted first
        self._linked_messages: OrderedDict[tuple[str, str], str] = OrderedDict()

    def _new_session_id(self, chat_id: str, now: datetime) -> str:
        return f"telegram-{chat_id}-{now.strftime('%Y%m%d-%H%M%S')}"

    def link_message(self, chat_id: str, message_id: str | int, session_id: str) -> None:
        """Associate an outgoing message with a session so replies can resume it."""
        key = (str(chat_id), str(message_id))
        self._linked_messages[key] = session_id
        self._linked_messages.move_to_end(key)
        while len(self._linked_messages) > self._max_linked_messages:
            self._linked_messages.popitem(last=False)

    def start_new(self, chat_id: str) -> str:
        """Explicitly begin a fresh session for a chat and return its ID."""
        now = self._clock()
        key = str(chat_id)
        session_id = self._new_session_id(key, now)
        self._sessions[key] = (session_id, now)
        return session_id

    def get_session_id(self, chat_id: str, reply_to_message_id: str | int | None = None) -> str:
        """Return the session ID to use for a new message in the given chat."""
        now = self._clock()
        key = str(chat_id)

        if reply_to_message_id is not None:
            linked = self._linked_messages.get((key, str(reply_to_message_id)))
            if linked is not None:
                self._sessions[key] = (linked, now)
                return linked

        current = self._sessions.get(key)
        if (
            current is None
            or self._idle_timeout.total_seconds() <= 0
            or now - current[1] > self._idle_timeout
        ):
            session_id = self._new_session_id(key, now)
        else:
            session_id = current[0]
        self._sessions[key] = (session_id, now)
        return session_id
