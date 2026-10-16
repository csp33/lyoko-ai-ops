"""Outbound Telegram operations mixed into the Telegram chat connector.

These methods send, edit, and request approval; they rely on ``self._app`` and
``self.default_chat_id`` provided by the concrete connector.
"""

import asyncio
import contextlib
import html
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from lyoko.domain.models.chat import ApprovalRequest, SentMessage
from lyoko.infrastructure.chat.formatting import markdown_to_telegram_html
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import RetryAfter

logger = logging.getLogger("lyoko.chat.telegram")

# A throttled send must still be delivered: wait for Telegram's RetryAfter (capped) and retry.
_MAX_FLOOD_WAIT_SECONDS = 45.0


async def _with_flood_retry(call: Callable[[], Awaitable[Any]], *, context: str) -> Any:
    """Run one Telegram call, waiting out flood control and retrying it once."""
    try:
        return await call()
    except RetryAfter as exc:
        retry_after = exc.retry_after
        total_seconds = getattr(retry_after, "total_seconds", None)
        seconds = total_seconds() if callable(total_seconds) else float(retry_after)
        wait = min(seconds + 1.0, _MAX_FLOOD_WAIT_SECONDS)
        logger.warning("Telegram flood control on %s; waiting %.0fs then retrying.", context, wait)
        await asyncio.sleep(wait)
        return await call()


def _to_sent_message(result: object, fallback_chat_id: str) -> SentMessage | None:
    """Convert a python-telegram-bot ``Message`` into a domain ``SentMessage`` reference."""
    message_id = getattr(result, "message_id", None)
    if not isinstance(message_id, int) or isinstance(message_id, bool):
        return None
    chat_id = getattr(result, "chat_id", None)
    if not isinstance(chat_id, (int, str)) or isinstance(chat_id, bool):
        chat_id = fallback_chat_id
    return SentMessage(chat_id=str(chat_id), message_id=str(message_id))


class TelegramOutboundMixin:
    """Send, edit, and request approval over a running Telegram application."""

    _app: Any
    default_chat_id: str | None
    _approval_message_html: dict[tuple[str, int], tuple[str, str | None]]
    _incident_to_approval_msg: dict[str, tuple[str, int]]

    async def send_message(
        self,
        chat_id: str,
        text: str,
        reply_to_message_id: str | int | None = None,
        message_thread_id: str | int | None = None,
        parse_mode: str = "HTML",
    ) -> SentMessage | None:
        """Send proactive text message to specific chat, group, or channel."""
        if not self._app or not self._app.bot:
            return None
        target = chat_id or self.default_chat_id
        if not target:
            return None

        msg_id = int(reply_to_message_id) if reply_to_message_id is not None else None
        thread_id = int(message_thread_id) if message_thread_id is not None else None

        if parse_mode == "HTML":
            formatted = markdown_to_telegram_html(text)
            try:
                sent = await _with_flood_retry(
                    lambda: self._app.bot.send_message(
                        chat_id=target,
                        text=formatted,
                        parse_mode="HTML",
                        reply_to_message_id=msg_id,
                        message_thread_id=thread_id,
                        allow_sending_without_reply=True,
                    ),
                    context="send HTML message",
                )
                logger.info(
                    "send_message (HTML) chat=%s reply_to=%s thread=%s -> message_id=%s.",
                    target,
                    msg_id,
                    thread_id,
                    getattr(sent, "message_id", None),
                )
                return _to_sent_message(sent, str(target))
            except Exception as exc:
                logger.warning(
                    "Failed to send HTML formatted message to Telegram, falling back: %s", exc
                )

        try:
            sent = await _with_flood_retry(
                lambda: self._app.bot.send_message(
                    chat_id=target,
                    text=text,
                    parse_mode=parse_mode if parse_mode != "HTML" else None,
                    reply_to_message_id=msg_id,
                    message_thread_id=thread_id,
                    allow_sending_without_reply=True,
                ),
                context="send message",
            )
        except Exception:
            sent = await _with_flood_retry(
                lambda: self._app.bot.send_message(
                    chat_id=target,
                    text=text,
                    reply_to_message_id=msg_id,
                    message_thread_id=thread_id,
                    allow_sending_without_reply=True,
                ),
                context="send message",
            )
        logger.info(
            "send_message (plain) chat=%s reply_to=%s thread=%s -> message_id=%s.",
            target,
            msg_id,
            thread_id,
            getattr(sent, "message_id", None),
        )
        return _to_sent_message(sent, str(target))

    async def edit_message(
        self,
        chat_id: str,
        message_id: str | int,
        text: str,
        parse_mode: str = "HTML",
    ) -> SentMessage | None:
        """Edit an existing proactive text message in a chat, group, or channel."""
        if not self._app or not self._app.bot:
            return None
        target = chat_id or self.default_chat_id
        if not target or message_id is None:
            return None

        try:
            msg_id = int(message_id)
        except (ValueError, TypeError):
            return None

        if parse_mode == "HTML":
            formatted = markdown_to_telegram_html(text)
            try:
                sent = await _with_flood_retry(
                    lambda: self._app.bot.edit_message_text(
                        chat_id=target,
                        message_id=msg_id,
                        text=formatted,
                        parse_mode="HTML",
                    ),
                    context="edit HTML message",
                )
                return _to_sent_message(sent, str(target)) or SentMessage(
                    chat_id=str(target), message_id=str(msg_id)
                )
            except Exception as exc:
                logger.warning(
                    "Failed to edit HTML formatted message in Telegram, falling back: %s", exc
                )

        try:
            sent = await _with_flood_retry(
                lambda: self._app.bot.edit_message_text(
                    chat_id=target,
                    message_id=msg_id,
                    text=text,
                    parse_mode=parse_mode if parse_mode != "HTML" else None,
                ),
                context="edit message",
            )
        except Exception as exc:
            logger.debug("Failed to edit plain message in Telegram: %s", exc)
            return None
        return _to_sent_message(sent, str(target)) or SentMessage(
            chat_id=str(target), message_id=str(msg_id)
        )

    async def send_approval_request(
        self,
        request: ApprovalRequest,
        message_thread_id: str | int | None = None,
        reply_to_message_id: str | int | None = None,
    ) -> SentMessage | None:
        """Send interactive approval prompt with inline action buttons to chat or channel."""
        if not self._app or not self._app.bot:
            return None
        target = request.chat_id or self.default_chat_id
        if not target:
            return None

        thread_id = int(message_thread_id) if message_thread_id is not None else None
        reply_to_id = int(reply_to_message_id) if reply_to_message_id is not None else None

        buttons = []
        for act in request.actions:
            cb_data = f"{act.action_id}:{request.incident_id}"
            buttons.append(InlineKeyboardButton(text=act.label, callback_data=cb_data))

        keyboard = InlineKeyboardMarkup([buttons]) if buttons else None
        text = f"🚨 <b>[APPROVAL REQUIRED]</b>\n\n<b>{html.escape(request.title)}</b>\n\n{markdown_to_telegram_html(request.details)}"
        try:
            sent = await self._app.bot.send_message(
                chat_id=target,
                text=text,
                reply_markup=keyboard,
                parse_mode="HTML",
                message_thread_id=thread_id,
                reply_to_message_id=reply_to_id,
                allow_sending_without_reply=True,
            )
        except Exception as exc:
            logger.warning("Failed to send approval request in HTML, falling back: %s", exc)
            plain_text = f"🚨 [APPROVAL REQUIRED]\n\n{request.title}\n\n{request.details}"
            sent = await self._app.bot.send_message(
                chat_id=target,
                text=plain_text,
                reply_markup=keyboard,
                message_thread_id=thread_id,
                reply_to_message_id=reply_to_id,
                allow_sending_without_reply=True,
            )
            text = plain_text
            parse_mode: str | None = None
        else:
            parse_mode = "HTML"
        if sent is not None:
            self._approval_message_html[(str(target), int(sent.message_id))] = (text, parse_mode)
            self._incident_to_approval_msg[request.incident_id] = (
                str(target),
                int(sent.message_id),
            )
        return _to_sent_message(sent, str(target))

    async def expire_approval_request(self, incident_id: str, reason: str = "Timed out") -> None:
        """Proactively revoke pending approval buttons and mark the message as timed out."""
        target_info = self._incident_to_approval_msg.pop(incident_id, None)
        if not target_info or not self._app or not self._app.bot:
            return
        chat_id, msg_id = target_info
        stored = self._approval_message_html.pop((chat_id, msg_id), None)
        if stored is not None:
            current_text, parse_mode = stored
        else:
            current_text, parse_mode = "", "HTML"
        status_text = f"⏱️ <b>Timed out</b> ({reason})."
        result_text = f"<b>Result:</b> {status_text}" if parse_mode else f"Result: {status_text}"
        new_text = f"{current_text}\n\n{result_text}" if current_text else result_text
        with contextlib.suppress(Exception):
            await self._app.bot.edit_message_text(
                chat_id=chat_id,
                message_id=msg_id,
                text=new_text,
                parse_mode=parse_mode,
                reply_markup=None,
            )
