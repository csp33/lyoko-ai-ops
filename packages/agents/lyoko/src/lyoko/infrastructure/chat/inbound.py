"""Inbound Telegram message handling mixed into the Telegram chat connector.

Turns an incoming text message or channel post into a domain ``IncomingMessage``, authorizes it,
shows typing/streaming feedback, and dispatches it to the registered message handlers.
"""

import asyncio
import contextlib
import inspect
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from lyoko.domain.models.chat import ChatUser, IncomingMessage
from lyoko.infrastructure.chat.formatting import markdown_to_telegram_html
from lyoko.infrastructure.chat.streamer import TelegramStreamingReply
from telegram import Update
from telegram.ext import ContextTypes

logger = logging.getLogger("lyoko.chat.telegram")


class TelegramInboundMixin:
    """Handle incoming Telegram messages and dispatch them to domain handlers."""

    _app: Any
    _message_handlers: list[Callable[[IncomingMessage], Awaitable[str | None]]]
    _active_streamers: dict[tuple[str, int], TelegramStreamingReply]

    async def _handle_telegram_message(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Handle incoming text messages and channel posts from Telegram."""
        msg = getattr(update, "message", None) or getattr(update, "effective_message", None)
        if not msg or not getattr(msg, "text", None):
            return

        chat = getattr(update, "effective_chat", None) or getattr(msg, "chat", None)
        chat_type = str(getattr(chat, "type", "")) if chat else ""
        chat_id = (
            str(chat.id)
            if chat and getattr(chat, "id", None) is not None and not hasattr(chat.id, "_mock_name")
            else ""
        )
        logger.info(
            "_handle_telegram_message: msg_id=%s, chat_type=%s, chat_id=%s, "
            "is_automatic_forward=%s, text=%r",
            getattr(msg, "message_id", None),
            chat_type,
            chat_id,
            getattr(msg, "is_automatic_forward", None),
            (str(msg.text).strip()[:80] if msg.text else None),
        )

        # Automatic forward from channel to discussion group - record the mapping so replies to
        # the original channel post can be threaded here, then do not process it as a user command.
        if getattr(msg, "is_automatic_forward", None) is True:
            self._record_channel_forward(msg)
            return

        chat = getattr(update, "effective_chat", None) or getattr(msg, "chat", None)
        user = getattr(update, "effective_user", None) or getattr(msg, "from_user", None)
        chat_type = str(getattr(chat, "type", "")) if chat else ""

        chat_id = (
            str(chat.id)
            if chat and getattr(chat, "id", None) is not None and not hasattr(chat.id, "_mock_name")
            else ""
        )
        user_id = (
            str(user.id)
            if user and getattr(user, "id", None) is not None and not hasattr(user.id, "_mock_name")
            else None
        )

        # Thread the reply into the same topic/thread as the incoming message
        message_thread_id = getattr(msg, "message_thread_id", None)

        # Check authorization (allowed user or allowed channel/chat)
        user_auth = self.is_user_authorized(user_id)
        chat_auth = self.is_chat_authorized(chat_id)

        if not user_auth and not chat_auth:
            logger.warning(
                "Unauthorized Telegram access attempt from user_id: %s, chat_id: %s",
                user_id,
                chat_id,
            )
            chat_type = str(getattr(chat, "type", "")) if chat else ""
            if chat_type != "channel":
                with contextlib.suppress(Exception):
                    await msg.reply_text(
                        "⛔ Access denied. Your user ID or chat is not authorized.",
                        reply_to_message_id=msg.message_id,
                        message_thread_id=message_thread_id,
                        allow_sending_without_reply=True,
                    )
            return

        # 1. React with an emoji (e.g. "👀") to acknowledge and indicate active processing
        try:
            if hasattr(msg, "set_reaction"):
                await msg.set_reaction(reaction="👀")
            elif (
                context
                and getattr(context, "bot", None)
                and hasattr(context.bot, "set_message_reaction")
            ):
                await context.bot.set_message_reaction(
                    chat_id=chat.id if chat else chat_id,
                    message_id=msg.message_id,
                    reaction="👀",
                )
        except Exception as reaction_err:
            # A missing reaction must never block the reply, but it should not fail silently.
            logger.warning(
                "Failed to set 👀 reaction on message %s in chat %s: %s: %s",
                getattr(msg, "message_id", None),
                chat_id,
                type(reaction_err).__name__,
                reaction_err,
            )

        # 2. Trigger typing status in chat
        with contextlib.suppress(Exception):
            if chat and hasattr(chat, "send_action"):
                await chat.send_action(action="typing")
            elif (
                context
                and getattr(context, "bot", None)
                and hasattr(context.bot, "send_chat_action")
            ):
                await context.bot.send_chat_action(
                    chat_id=chat.id if chat else chat_id,
                    action="typing",
                )

        # 3. Handle commands (no streaming placeholder for commands)
        text = str(msg.text).strip()
        if text.startswith("/feedback") or text.startswith("/teach"):
            await self._handle_feedback_command(msg, text)
            return
        if text.startswith("/forget"):
            await self._handle_forget_command(msg, text)
            return
        if text.startswith("/memories"):
            await self._handle_memories_command(msg, text)
            return

        # 4. Stream the answer into a real placeholder message, threaded to the incoming message
        bot_instance = (
            getattr(context, "bot", None)
            if context and getattr(context, "bot", None)
            else (self._app.bot if self._app else None)
        )
        # A channel post may reach us before its discussion-group forward; wait for the mapping so
        # the reply is threaded under the alert instead of landing in the group's general topic.
        if chat_type == "channel" and self.discussion_group_id:
            logger.info(
                "Channel post %s in %s: waiting up to %.1fs for its discussion-group forward.",
                msg.message_id,
                chat_id,
                self.discussion_mapping_timeout,
            )
            mapped = await self._await_discussion_mapping(chat_id, msg.message_id)
            logger.info(
                "Channel post %s discussion mapping %s.",
                msg.message_id,
                "found" if mapped else "NOT found (will reply without a thread)",
            )
        target_chat_id, target_reply_id, target_thread_id = self._resolve_reply_target(
            msg, chat_id, chat_type, message_thread_id
        )
        logger.info(
            "Reply target: chat=%s reply_to=%s thread=%s (origin chat=%s type=%s msg=%s).",
            target_chat_id,
            target_reply_id,
            target_thread_id,
            chat_id,
            chat_type,
            getattr(msg, "message_id", None),
        )
        streamer = TelegramStreamingReply(
            bot=bot_instance,
            chat_id=target_chat_id,
            reply_to_message_id=target_reply_id,
            message_thread_id=target_thread_id,
        )
        await streamer.start()
        logger.info(
            "Streaming placeholder for msg %s: chat=%s message_id=%s disabled=%s.",
            getattr(msg, "message_id", None),
            target_chat_id,
            streamer.message_id,
            streamer.disabled,
        )
        streamer_key = (str(target_chat_id), streamer.draft_id)
        self._active_streamers[streamer_key] = streamer
        try:
            username = None
            if (
                user
                and getattr(user, "username", None)
                and not hasattr(user.username, "_mock_name")
            ):
                username = str(user.username)
            elif (
                chat
                and getattr(chat, "username", None)
                and not hasattr(chat.username, "_mock_name")
            ):
                username = str(chat.username)

            first_name = "User"
            if (
                user
                and getattr(user, "first_name", None)
                and not hasattr(user.first_name, "_mock_name")
            ):
                first_name = str(user.first_name)
            elif chat and getattr(chat, "title", None) and not hasattr(chat.title, "_mock_name"):
                first_name = str(chat.title)

            reply_to_message_id: str | None = None
            replied = getattr(msg, "reply_to_message", None)
            replied_id = getattr(replied, "message_id", None) if replied is not None else None
            if isinstance(replied_id, int) and not isinstance(replied_id, bool):
                reply_to_message_id = str(replied_id)

            incoming = IncomingMessage(
                message_id=str(msg.message_id),
                # Talk in the resolved target (the linked discussion group's thread for a channel
                # post), so every downstream message - progress, approvals, report - threads there
                # instead of landing in the channel or the group's general topic.
                chat_id=str(target_chat_id),
                user=ChatUser(
                    user_id=user_id or chat_id,
                    username=username,
                    first_name=first_name,
                ),
                text=text,
                reply_to_message_id=reply_to_message_id,
                message_thread_id=(str(target_thread_id) if target_thread_id is not None else None),
            )

            answered = False
            for handler in self._message_handlers:
                try:
                    accepts_on_status = False
                    try:
                        sig = inspect.signature(handler)
                        accepts_on_status = "on_status" in sig.parameters or any(
                            p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()
                        )
                    except (ValueError, TypeError):
                        pass

                    coro = (
                        handler(incoming, on_status=streamer.set_status)
                        if accepts_on_status
                        else handler(incoming)
                    )
                    handler_task = asyncio.create_task(coro)
                    streamer.bind_task(handler_task)
                    try:
                        reply = await handler_task
                    except (asyncio.CancelledError, GeneratorExit):
                        if streamer.is_stopped():
                            logger.info(
                                "Streaming reply for msg %s was stopped by the operator.", chat_id
                            )
                            await streamer.finalize_stopped()
                            return
                        raise

                    if not reply:
                        logger.info(
                            "Handler %s produced no reply for msg %s in chat %s.",
                            getattr(handler, "__qualname__", handler),
                            getattr(msg, "message_id", None),
                            chat_id,
                        )
                        continue

                    answered = True
                    if streamer.is_stopped():
                        logger.info(
                            "Streaming reply for msg %s was stopped by the operator.", chat_id
                        )
                        await streamer.finalize_stopped()
                        return
                    finalized = await streamer.finalize(markdown_to_telegram_html(reply))
                    logger.info(
                        "Streaming finalize for msg %s in chat %s: %s (placeholder message_id=%s).",
                        getattr(msg, "message_id", None),
                        target_chat_id,
                        "edited in place" if finalized else "FAILED, using direct reply",
                        streamer.message_id,
                    )
                    if not finalized:
                        await self._reply_to_chat(msg, reply, chat_id, chat_type, message_thread_id)
                except (asyncio.CancelledError, GeneratorExit):
                    logger.info("Message generation cancelled by user in chat %s", chat_id)
                    return
                except Exception as exc:
                    logger.error("Error executing message handler: %s", exc, exc_info=True)
                    answered = True
                    if not await streamer.finalize(f"⚠️ Error processing request: {exc}"):
                        logger.warning(
                            "Streaming finalize of the error notice failed for msg %s in chat %s; "
                            "using direct reply fallback.",
                            getattr(msg, "message_id", None),
                            chat_id,
                        )
                        with contextlib.suppress(Exception):
                            await msg.reply_text(
                                f"⚠️ Error processing request: {exc}",
                                reply_to_message_id=msg.message_id,
                                message_thread_id=message_thread_id,
                                allow_sending_without_reply=True,
                            )
            if not answered:
                logger.info(
                    "No handler answered msg %s in chat %s; discarding placeholder %s.",
                    getattr(msg, "message_id", None),
                    chat_id,
                    streamer.message_id,
                )
                await streamer.discard()
        finally:
            self._active_streamers.pop(streamer_key, None)
