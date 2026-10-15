"""Entry point of operator messages into the LYOKO graph."""

import logging
from collections.abc import Callable
from typing import Any

from lyoko.application.chat.sessions import ChatSessionTracker
from lyoko.application.chat.use_cases.process_message import (
    ProcessChatMessageUseCase,
)
from lyoko.application.incidents.use_cases.enrich_trace_cost import (
    EnrichTraceCostUseCase,
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
        session_tracker: ChatSessionTracker | None = None,
        tracer: TracerInterface | None = None,
        enrich_trace_cost: EnrichTraceCostUseCase | None = None,
        chat_manager: Any = None,
    ) -> None:
        self._use_case = ProcessChatMessageUseCase(
            graph_provider=graph_provider,
            session_tracker=session_tracker or ChatSessionTracker(),
            tracer=tracer,
            enrich_trace_cost=enrich_trace_cost,
            chat_manager=chat_manager,
        )

    @property
    def tracer(self) -> TracerInterface | None:
        return self._use_case.tracer

    @property
    def session_tracker(self) -> ChatSessionTracker:
        return self._use_case.session_tracker

    @property
    def enrich_trace_cost(self) -> EnrichTraceCostUseCase | None:
        return self._use_case.enrich_trace_cost

    async def handle_message(
        self,
        message: IncomingMessage,
        on_status: Any = None,
    ) -> str:
        """Process an incoming chat message and return the reply for the operator."""
        return await self._use_case.execute(message, on_status=on_status)

    def _trace_config(self, message: IncomingMessage, session_id: str) -> dict[str, Any]:
        return self._use_case._trace_config(message, session_id)
