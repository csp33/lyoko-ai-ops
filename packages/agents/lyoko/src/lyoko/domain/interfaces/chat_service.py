"""Abstract interface for chat orchestration and messaging."""

from abc import ABC, abstractmethod

from lyoko.domain.interfaces.chat_connector import (
    ApprovalHandler,
    ChatConnector,
    MessageHandler,
)
from lyoko.domain.models.chat import ApprovalRequest, SentMessage


class ChatServiceInterface(ABC):
    """Abstract interface for managing chat connectors and message dispatch."""

    @abstractmethod
    def add_connector(self, connector: ChatConnector) -> None:
        """Register an additional ChatConnector instance."""

    @abstractmethod
    def register_message_handler(self, handler: MessageHandler) -> None:
        """Route incoming messages from every registered connector to handler."""

    @abstractmethod
    def register_approval_handler(self, handler: ApprovalHandler) -> None:
        """Route incoming approval/button responses to handler."""

    @abstractmethod
    async def start_all(self) -> None:
        """Start all registered chat connectors."""

    @abstractmethod
    async def stop_all(self) -> None:
        """Stop all registered chat connectors."""

    @abstractmethod
    async def broadcast_message(
        self,
        chat_id: str,
        text: str,
        reply_to_message_id: str | int | None = None,
        message_thread_id: str | int | None = None,
        parse_mode: str = "Markdown",
        session_id: str | None = None,
    ) -> list[SentMessage | None]:
        """Broadcast a message through all registered connectors."""

    @abstractmethod
    async def broadcast_approval_request(
        self,
        request: ApprovalRequest,
        message_thread_id: str | int | None = None,
        reply_to_message_id: str | int | None = None,
        session_id: str | None = None,
    ) -> list[SentMessage | None]:
        """Broadcast an approval request through all registered connectors."""

    @abstractmethod
    async def edit_message(
        self,
        chat_id: str,
        message_id: str | int,
        text: str,
        parse_mode: str = "Markdown",
    ) -> list[SentMessage | None]:
        """Edit a message previously sent via registered connectors."""

    async def expire_approval_request(  # noqa: B027
        self, incident_id: str, reason: str = "Timed out"
    ) -> None:
        """Revoke active approval prompts for an incident and clear interactive buttons."""
