"""Observability session management for chat conversations.

Langfuse sessions are plain groupings of traces sharing an ID and cannot be closed
explicitly. To keep sessions meaningful (one conversation topic or one incident per
session), a chat's session ID is derived as follows:

1. Replying to a message linked to a session (e.g. an incident alert or approval request)
   continues that session, so follow-up questions about an alert stay attached to it.
2. In a thread/topic where the thread ID matches a linked message (e.g. discussion group
   comments under an incident alert post), the thread continues that incident's session.
3. In a forum topic or thread with a dedicated ``thread_id``, each topic maintains its own
   independent session, so parallel conversations in the same Telegram chat do not leak
   into each other.
4. ``start_new`` (the ``/new`` command) explicitly begins a fresh session for the specific
   chat and thread.
5. Otherwise the current session for the chat/thread is reused until it has been idle for
   longer than the idle timeout, after which a new session is started.
"""

from collections import OrderedDict
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

DEFAULT_SESSION_IDLE_TIMEOUT_SECONDS = 900
MAX_LINKED_MESSAGES = 1000


class ChatSessionTracker:
    """Track the active observability session for each chat and thread."""

    def __init__(
        self,
        idle_timeout_seconds: float = DEFAULT_SESSION_IDLE_TIMEOUT_SECONDS,
        clock: Callable[[], datetime] | None = None,
        max_linked_messages: int = MAX_LINKED_MESSAGES,
    ) -> None:
        self._idle_timeout = timedelta(seconds=idle_timeout_seconds)
        self._clock = clock or (lambda: datetime.now(UTC))
        self._max_linked_messages = max_linked_messages
        # (chat_id, thread_id) -> (session_id, last_activity)
        self._sessions: dict[tuple[str, str | None], tuple[str, datetime]] = {}
        # (chat_id, message_id) -> session_id, oldest entries evicted first
        self._linked_messages: OrderedDict[tuple[str, str], str] = OrderedDict()

    def _new_session_id(
        self,
        chat_id: str,
        now: datetime,
        thread_id: str | int | None = None,
    ) -> str:
        timestamp = now.strftime("%Y%m%d-%H%M%S")
        if thread_id is not None:
            return f"telegram-{chat_id}-{thread_id}-{timestamp}"
        return f"telegram-{chat_id}-{timestamp}"

    def link_message(self, chat_id: str, message_id: str | int, session_id: str) -> None:
        """Associate a message with a session so replies can resume it."""
        key = (str(chat_id), str(message_id))
        self._linked_messages[key] = session_id
        self._linked_messages.move_to_end(key)
        while len(self._linked_messages) > self._max_linked_messages:
            self._linked_messages.popitem(last=False)

    def get_linked_session(self, chat_id: str, message_id: str | int) -> str | None:
        """Return the session ID linked to a specific message, if any."""
        return self._linked_messages.get((str(chat_id), str(message_id)))

    def start_new(self, chat_id: str, thread_id: str | int | None = None) -> str:
        """Explicitly begin a fresh session for a chat/thread and return its ID."""
        now = self._clock()
        cid = str(chat_id)
        tid = str(thread_id) if thread_id is not None else None
        key = (cid, tid)
        session_id = self._new_session_id(cid, now, thread_id=tid)
        self._sessions[key] = (session_id, now)
        return session_id

    def get_session_id(
        self,
        chat_id: str,
        reply_to_message_id: str | int | None = None,
        thread_id: str | int | None = None,
    ) -> str:
        """Return the session ID to use for a message in the given chat/thread."""
        now = self._clock()
        cid = str(chat_id)
        tid = str(thread_id) if thread_id is not None else None
        key = (cid, tid)

        if reply_to_message_id is not None:
            linked = self._linked_messages.get((cid, str(reply_to_message_id)))
            if linked is not None:
                self._sessions[key] = (linked, now)
                return linked

        if tid is not None:
            linked_thread = self._linked_messages.get((cid, tid))
            if linked_thread is not None:
                self._sessions[key] = (linked_thread, now)
                return linked_thread

        current = self._sessions.get(key)
        if (
            current is None
            or self._idle_timeout.total_seconds() <= 0
            or now - current[1] > self._idle_timeout
        ):
            session_id = self._new_session_id(cid, now, thread_id=tid)
        else:
            session_id = current[0]
        self._sessions[key] = (session_id, now)
        return session_id
