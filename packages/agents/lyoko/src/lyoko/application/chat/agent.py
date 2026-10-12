"""Entry point of operator messages into the LYOKO graph."""

import logging
from collections.abc import Callable
from typing import Any

from lyoko.application.chat.history import ChatHistoryTracker
from lyoko.application.chat.sessions import (
    DEFAULT_SESSION_IDLE_TIMEOUT_SECONDS,
    ChatSessionTracker,
)
from lyoko.application.chat.use_cases.process_message import (
    ProcessChatMessageUseCase,
)
from lyoko.domain.interfaces.tracer import TracerInterface
from lyoko.domain.models.chat import IncomingMessage

logger = logging.getLogger("lyoko.chat_agent")

TRACE_NAME = "telegram-chat-interaction"


class InteractiveChatAgent:
    """Turns each chat message into one run of the LYOKO graph and returns the reply.

    Delegates processing to ``ProcessChatMessageUseCase``.
    """

    def __init__(
        self,
        graph_provider: Callable[[], Any],
        tracer: TracerInterface | None = None,
        session_idle_timeout_seconds: float = DEFAULT_SESSION_IDLE_TIMEOUT_SECONDS,
        session_tracker: ChatSessionTracker | None = None,
        history_tracker: ChatHistoryTracker | None = None,
    ) -> None:
        self._use_case = ProcessChatMessageUseCase(
            graph_provider=graph_provider,
            tracer=tracer,
            session_idle_timeout_seconds=session_idle_timeout_seconds,
            session_tracker=session_tracker,
            history_tracker=history_tracker,
        )

    @property
    def tracer(self) -> TracerInterface | None:
        return self._use_case.tracer

    @property
    def session_tracker(self) -> ChatSessionTracker:
        return self._use_case.session_tracker

    @property
    def history_tracker(self) -> ChatHistoryTracker:
        return self._use_case.history_tracker

    async def handle_message(
        self,
        message: IncomingMessage,
        on_status: Any = None,
    ) -> str:
        """Process an incoming chat message and return the reply for the operator."""
        return await self._use_case.execute(message, on_status=on_status)

    def _trace_config(self, message: IncomingMessage, session_id: str) -> dict[str, Any]:
        return self._use_case._trace_config(message, session_id)
