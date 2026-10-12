"""Telegram chat connector adapter implementation using python-telegram-bot."""

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable

from lyoko.domain.interfaces.chat_connector import ChatConnector
from lyoko.domain.interfaces.embeddings import EmbeddingsServiceInterface
from lyoko.domain.interfaces.lock import DistributedLock
from lyoko.domain.interfaces.memory import MemoryRepositoryInterface
from lyoko.domain.models.chat import ApprovalResponse, IncomingMessage
from lyoko.infrastructure.chat.callbacks import TelegramCallbackMixin
from lyoko.infrastructure.chat.feedback import TelegramFeedbackMixin
from lyoko.infrastructure.chat.inbound import TelegramInboundMixin
from lyoko.infrastructure.chat.outbound import TelegramOutboundMixin
from lyoko.infrastructure.chat.replies import TelegramReplyMixin
from lyoko.infrastructure.chat.streamer import TelegramStreamingReply
from pydantic import SecretStr
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    filters,
)

logger = logging.getLogger("lyoko.chat.telegram")


class TelegramConnector(
    TelegramInboundMixin,
    TelegramFeedbackMixin,
    TelegramCallbackMixin,
    TelegramReplyMixin,
    TelegramOutboundMixin,
    ChatConnector,
):
    """Telegram adapter implementing ChatConnector port using python-telegram-bot.

    Supports private 1-on-1 chats, group chats, and Telegram channels for
    interactive assistant communication, alert broadcasting, HITL approvals, and memory feedback.
    """

    def __init__(
        self,
        bot_token: str | SecretStr | None,
        allowed_user_ids: set[str] | list[str] | str | None = None,
        allowed_chat_ids: set[str] | list[str] | str | None = None,
        default_chat_id: str | None = None,
        discussion_group_id: str | int | None = None,
        memory_repository: MemoryRepositoryInterface | None = None,
        embeddings_service: EmbeddingsServiceInterface | None = None,
        lock: DistributedLock | None = None,
    ) -> None:
        if isinstance(bot_token, SecretStr):
            self.bot_token = bot_token.get_secret_value()
        elif bot_token:
            self.bot_token = str(bot_token)
        else:
            self.bot_token = ""

        if isinstance(allowed_user_ids, str):
            self.allowed_user_ids = {
                uid.strip() for uid in allowed_user_ids.split(",") if uid.strip()
            }
        elif allowed_user_ids:
            self.allowed_user_ids = {
                str(uid).strip() for uid in allowed_user_ids if str(uid).strip()
            }
        else:
            self.allowed_user_ids = set()

        if isinstance(allowed_chat_ids, str):
            self.allowed_chat_ids = {
                cid.strip() for cid in allowed_chat_ids.split(",") if cid.strip()
            }
        elif allowed_chat_ids:
            self.allowed_chat_ids = {
                str(cid).strip() for cid in allowed_chat_ids if str(cid).strip()
            }
        else:
            self.allowed_chat_ids = set()

        self.default_chat_id = str(default_chat_id).strip() if default_chat_id else None
        if self.default_chat_id:
            self.allowed_chat_ids.add(self.default_chat_id)

        self.discussion_group_id = str(discussion_group_id).strip() if discussion_group_id else None
        if self.discussion_group_id:
            self.allowed_chat_ids.add(self.discussion_group_id)

        self.memory_repository = memory_repository
        self.embeddings_service = embeddings_service
        self.lock = lock
        self._leader_polling_task: asyncio.Task[None] | None = None

        # The channel post and its discussion-group forward can arrive in either order; bound how
        # long a channel post waits for the forward so its reply threads under the alert.
        self.discussion_mapping_timeout = 6.0
        self.discussion_mapping_interval = 0.2

        self._channel_to_discussion: dict[tuple[str, int], int] = {}
        self._message_handlers: list[Callable[[IncomingMessage], Awaitable[str | None]]] = []
        self._approval_handlers: list[Callable[[ApprovalResponse], Awaitable[None]]] = []
        self._active_streamers: dict[tuple[str, int], TelegramStreamingReply] = {}
        # Approval prompts must be edited back with their original markup, because Telegram
        # returns an answered message's `text` unformatted, which would drop every bold label.
        self._approval_message_html: dict[tuple[str, int], tuple[str, str | None]] = {}
        self._app: Application | None = None

    def is_user_authorized(self, user_id: str | int | None) -> bool:
        """Verify whether user_id is explicitly allowed by whitelist."""
        if not self.allowed_user_ids or user_id is None:
            return False
        return str(user_id).strip() in self.allowed_user_ids

    def is_chat_authorized(self, chat_id: str | int | None) -> bool:
        """Verify whether chat_id (e.g. channel or group) is explicitly allowed."""
        if not self.allowed_chat_ids or chat_id is None:
            return False
        return str(chat_id).strip() in self.allowed_chat_ids

    def register_message_handler(
        self, handler: Callable[[IncomingMessage], Awaitable[str | None]]
    ) -> None:
        """Register a callback for incoming messages."""
        self._message_handlers.append(handler)

    def register_approval_handler(
        self, handler: Callable[[ApprovalResponse], Awaitable[None]]
    ) -> None:
        """Register a callback for approval button responses."""
        self._approval_handlers.append(handler)

    async def start(self) -> None:
        """Initialize and start Telegram application and polling loop."""
        if not self.bot_token:
            logger.warning("TelegramConnector not started: missing bot token")
            return

        # Updates are processed one at a time by default. A message handler that waits for an
        # approval would then block the very button click that grants it, so handle them
        # concurrently.
        self._app = Application.builder().token(self.bot_token).concurrent_updates(True).build()
        self._app.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, self._handle_telegram_message)
        )
        self._app.add_handler(CommandHandler("start", self._handle_telegram_message))
        self._app.add_handler(CommandHandler("help", self._handle_telegram_message))
        self._app.add_handler(CommandHandler("new", self._handle_telegram_message))
        self._app.add_handler(CommandHandler("feedback", self._handle_telegram_message))
        self._app.add_handler(CommandHandler("teach", self._handle_telegram_message))
        self._app.add_handler(CommandHandler("forget", self._handle_telegram_message))
        self._app.add_handler(CommandHandler("memories", self._handle_telegram_message))
        self._app.add_handler(MessageHandler(filters.COMMAND, self._handle_telegram_message))

        self._app.add_handler(CallbackQueryHandler(self._handle_callback_query))

        await self._app.initialize()
        await self._app.start()

        # If discussion group was not explicitly configured, try to discover linked chat from default channel
        if not self.discussion_group_id and self.default_chat_id:
            try:
                chat_info = await self._app.bot.get_chat(self.default_chat_id)
                if getattr(chat_info, "linked_chat_id", None):
                    self.discussion_group_id = str(chat_info.linked_chat_id)
                    self.allowed_chat_ids.add(self.discussion_group_id)
                    logger.info(
                        "Discovered linked discussion group %s for channel %s",
                        self.discussion_group_id,
                        self.default_chat_id,
                    )
            except Exception as exc:
                logger.debug("Could not auto-discover linked discussion group: %s", exc)

        if self.lock is not None:
            self._leader_polling_task = asyncio.create_task(self._poll_with_leader_lock())
            logger.info("TelegramConnector registered leader election task for polling.")
        else:
            if self._app.updater:
                await self._app.updater.start_polling()
            logger.info("TelegramConnector started polling updates.")

    async def _poll_with_leader_lock(self) -> None:
        """Wait for distributed leader lock before starting the Telegram polling loop."""
        try:
            acquired = await self.lock.acquire()
            if not acquired:
                logger.info("Telegram leader lock was not acquired, exiting polling loop.")
                return

            if self._app and self._app.updater and not self._app.updater.running:
                await self._app.updater.start_polling()
                logger.info("TelegramConnector acquired leader lock and started polling updates.")
        except asyncio.CancelledError:
            logger.debug("Telegram leader polling task cancelled.")
            raise
        except Exception as exc:
            logger.error("Error in Telegram leader polling task: %s", exc, exc_info=True)

    async def stop(self) -> None:
        """Gracefully stop polling and terminate Telegram application."""
        if self._leader_polling_task and not self._leader_polling_task.done():
            self._leader_polling_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._leader_polling_task
            self._leader_polling_task = None

        if self._app:
            if self._app.updater and self._app.updater.running:
                await self._app.updater.stop()
            await self._app.stop()
            await self._app.shutdown()
            logger.info("TelegramConnector stopped.")

        if self.lock and self.lock.is_locked:
            await self.lock.release()
