from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable

from lyoko.domain.models.chat import (
    ApprovalRequest,
    ApprovalResponse,
    SentMessage,
)

MessageHandler = Callable[..., Awaitable[str | None]]
ApprovalHandler = Callable[[ApprovalResponse], Awaitable[None]]


class ChatConnector(ABC):
    @abstractmethod
    async def start(self) -> None:
        pass

    @abstractmethod
    async def stop(self) -> None:
        pass

    @abstractmethod
    async def send_message(
        self,
        chat_id: str,
        text: str,
        reply_to_message_id: str | int | None = None,
        message_thread_id: str | int | None = None,
        parse_mode: str = "Markdown",
    ) -> SentMessage | None:
        """Send a message and return a reference to it when the platform exposes one."""

    @abstractmethod
    async def edit_message(
        self,
        chat_id: str,
        message_id: str | int,
        text: str,
        parse_mode: str = "Markdown",
    ) -> SentMessage | None:
        """Edit an existing message previously sent by the connector."""

    @abstractmethod
    async def send_approval_request(
        self,
        request: ApprovalRequest,
        message_thread_id: str | int | None = None,
        reply_to_message_id: str | int | None = None,
    ) -> SentMessage | None:
        """Send an approval prompt and return a reference to it when available."""

    async def expire_approval_request(  # noqa: B027
        self, incident_id: str, reason: str = "Timed out"
    ) -> None:
        """Revoke interactive approval buttons when an approval times out."""

    @abstractmethod
    def register_message_handler(self, handler: MessageHandler) -> None:
        pass

    @abstractmethod
    def register_approval_handler(self, handler: ApprovalHandler) -> None:
        pass
