"""Chat manager composite adapter implementing ChatServiceInterface."""

import logging

from lyoko.application.chat.sessions import ChatSessionTracker
from lyoko.domain.interfaces.chat_connector import (
    ApprovalHandler,
    ChatConnector,
    MessageHandler,
)
from lyoko.domain.interfaces.chat_service import ChatServiceInterface
from lyoko.domain.models.chat import ApprovalRequest, SentMessage

logger = logging.getLogger("lyoko.infrastructure.chat.manager")


class ChatManager(ChatServiceInterface):
    """Manages active chat connectors and orchestrates broadcast messaging.

    When a ``session_tracker`` is provided, outgoing incident messages are linked to their
    observability session so that user replies to them continue the same session.
    """

    def __init__(
        self,
        connectors: list[ChatConnector] | None = None,
        session_tracker: ChatSessionTracker | None = None,
    ) -> None:
        self.connectors: list[ChatConnector] = connectors if connectors is not None else []
        self.session_tracker = session_tracker

    def add_connector(self, connector: ChatConnector) -> None:
        """Register an additional ChatConnector instance."""
        self.connectors.append(connector)

    def register_message_handler(self, handler: MessageHandler) -> None:
        """Route incoming messages from every registered connector to ``handler``."""
        for conn in self.connectors:
            conn.register_message_handler(handler)

    def register_approval_handler(self, handler: ApprovalHandler) -> None:
        """Route incoming approval/button responses from every registered connector to ``handler``."""
        for conn in self.connectors:
            conn.register_approval_handler(handler)

    async def start_all(self) -> None:
        """Start all registered chat connectors."""
        for conn in self.connectors:
            await conn.start()

    async def stop_all(self) -> None:
        """Stop all registered chat connectors."""
        for conn in self.connectors:
            await conn.stop()

    def _link_sent_message(self, sent: object, session_id: str | None) -> None:
        if self.session_tracker is None or not session_id or not isinstance(sent, SentMessage):
            return
        self.session_tracker.link_message(sent.chat_id, sent.message_id, session_id)

    async def broadcast_message(
        self,
        chat_id: str,
        text: str,
        reply_to_message_id: str | int | None = None,
        parse_mode: str = "Markdown",
        session_id: str | None = None,
        message_thread_id: str | int | None = None,
    ) -> list[SentMessage]:
        """Send message across all registered connectors with fault tolerance.

        If ``session_id`` is given, replies to the sent message continue that session.
        """
        results: list[SentMessage] = []
        for conn in self.connectors:
            try:
                sent = await conn.send_message(
                    chat_id=chat_id,
                    text=text,
                    reply_to_message_id=reply_to_message_id,
                    parse_mode=parse_mode,
                    message_thread_id=message_thread_id,
                )
                self._link_sent_message(sent, session_id)
                if isinstance(sent, SentMessage):
                    results.append(sent)
            except Exception as e:
                logger.error("Error broadcasting message via connector: %s", e)
        return results

    async def edit_message(
        self,
        chat_id: str,
        message_id: str | int,
        text: str,
        parse_mode: str = "Markdown",
    ) -> list[SentMessage]:
        """Edit an existing message across all registered connectors with fault tolerance."""
        results: list[SentMessage] = []
        for conn in self.connectors:
            try:
                sent = await conn.edit_message(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=text,
                    parse_mode=parse_mode,
                )
                if isinstance(sent, SentMessage):
                    results.append(sent)
            except Exception as e:
                logger.error("Error editing message via connector: %s", e)
        return results

    async def broadcast_approval_request(
        self,
        request: ApprovalRequest,
        message_thread_id: str | int | None = None,
        reply_to_message_id: str | int | None = None,
    ) -> None:
        """Send approval request across all registered connectors with fault tolerance.

        Replies to the approval message continue the request's session, which defaults to the
        incident ID.
        """
        for conn in self.connectors:
            try:
                sent = await conn.send_approval_request(
                    request,
                    message_thread_id=message_thread_id,
                    reply_to_message_id=reply_to_message_id,
                )
                self._link_sent_message(sent, request.session_id or request.incident_id)
            except Exception as e:
                logger.error("Error broadcasting approval request via connector: %s", e)

    async def expire_approval_request(self, incident_id: str, reason: str = "Timed out") -> None:
        """Notify connectors to revoke the approval prompt and clear interactive buttons."""
        for conn in self.connectors:
            if hasattr(conn, "expire_approval_request"):
                try:
                    await conn.expire_approval_request(incident_id, reason=reason)
                except Exception as exc:
                    logger.debug("Failed to expire approval request on connector: %s", exc)
