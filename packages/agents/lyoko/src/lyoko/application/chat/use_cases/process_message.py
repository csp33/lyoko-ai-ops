"""Use case for processing incoming chat messages through session, history, and graph execution."""

import logging
import uuid
from collections.abc import Callable
from typing import Any

from lyoko.application.chat.history import ChatHistoryTracker
from lyoko.application.chat.sessions import (
    DEFAULT_SESSION_IDLE_TIMEOUT_SECONDS,
    ChatSessionTracker,
)
from lyoko.domain.interfaces.tracer import TracerInterface
from lyoko.domain.models.chat import IncomingMessage
from lyoko.domain.models.state import EVENT_MESSAGE

logger = logging.getLogger("lyoko.application.chat.use_cases.process_message")

NEW_SESSION_COMMAND = "/new"
TRACE_NAME = "telegram-chat-interaction"


def is_new_session_command(text: str) -> bool:
    """Return True for ``/new`` (optionally addressed as ``/new@botname``)."""
    first_token = text.strip().split(maxsplit=1)[0].lower() if text.strip() else ""
    return first_token == NEW_SESSION_COMMAND or first_token.startswith(f"{NEW_SESSION_COMMAND}@")


class ProcessChatMessageUseCase:
    """Orchestrates an incoming chat message into session state, tracing, and graph invocation."""

    def __init__(
        self,
        graph_provider: Callable[[], Any],
        tracer: TracerInterface | None = None,
        session_idle_timeout_seconds: float = DEFAULT_SESSION_IDLE_TIMEOUT_SECONDS,
        session_tracker: ChatSessionTracker | None = None,
        history_tracker: ChatHistoryTracker | None = None,
    ) -> None:
        self._graph_provider = graph_provider
        self.tracer = tracer
        self.session_tracker = session_tracker or ChatSessionTracker(session_idle_timeout_seconds)
        self.history_tracker = history_tracker or ChatHistoryTracker()

    def _trace_config(self, message: IncomingMessage, session_id: str) -> dict[str, Any]:
        tags = ["telegram", "interactive-chat", f"chat:{message.chat_id}"]
        metadata = {
            "chat_id": message.chat_id,
            "username": message.user.username,
            "message_id": message.message_id,
        }
        if self.tracer is not None:
            return self.tracer.get_trace_config(
                session_id=session_id,
                user_id=str(message.user.user_id),
                trace_name=TRACE_NAME,
                tags=tags,
                metadata=metadata,
            )
        return {"run_name": TRACE_NAME, "tags": tags, "metadata": metadata}

    async def execute(
        self,
        message: IncomingMessage,
        on_status: Any = None,
    ) -> str:
        """Process an incoming chat message and return the reply for the operator."""
        logger.info("Processing chat message from user %s: %s", message.user.user_id, message.text)
        if is_new_session_command(message.text):
            session_id = self.session_tracker.start_new(message.chat_id)
            self.history_tracker.clear(message.chat_id, message.message_thread_id)
            logger.info("Started new session %s for chat %s", session_id, message.chat_id)
            return "🆕 Started a new session. Previous context will not be grouped with this one."

        session_id = self.session_tracker.get_session_id(
            message.chat_id, reply_to_message_id=message.reply_to_message_id
        )
        history_context = self.history_tracker.get_history_context(
            message.chat_id, message.message_thread_id
        )
        event_id = f"chat-{uuid.uuid4().hex[:8]}"

        state = {
            "event_type": EVENT_MESSAGE,
            "event_id": event_id,
            "session_id": session_id,
            "chat_id": message.chat_id,
            "message_thread_id": message.message_thread_id,
            "text": message.text,
            "history_context": history_context,
            "labels": {},
            "annotations": {},
        }
        config = self._trace_config(message, session_id)
        config.setdefault("configurable", {})["thread_id"] = event_id
        if on_status is not None:
            config["configurable"]["on_status"] = on_status

        try:
            result = await self._graph_provider().ainvoke(state, config=config)
            self.history_tracker.record_turn(
                message.chat_id, message.text, result.get("reply", ""), message.message_thread_id
            )
        except Exception as exc:
            logger.error("Failed to process chat message: %s", exc, exc_info=True)
            self.history_tracker.record_turn(
                message.chat_id, message.text, f"Error: {exc}", message.message_thread_id
            )
            return f"⚠️ Error processing your question: {exc}"
        return str(result.get("reply") or "")
