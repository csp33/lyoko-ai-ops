"""Streaming Telegram replies via an editable progress-log message.

Telegram's native ``sendMessageDraft`` only targets private chats and shows an ephemeral
30-second preview that refused to stay alive here. Instead this sends a real message and edits it
in place: it grows a checklist of the steps actually running (the specialist and tools the agent
calls), then replaces the whole message with the final formatted answer. Works in private chats,
groups, channels, and channel discussion threads.
"""

import asyncio
import contextlib
import logging
import re
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import RetryAfter

logger = logging.getLogger("lyoko.chat.telegram")

INITIAL_STATUS = "Thinking…"
STOP_CALLBACK_PREFIX = "stop"
MAX_MESSAGE_LENGTH = 4096
_TAG_PATTERN = re.compile(r"<[^>]+>")
# Telegram throttles edits to one message and answers flood control with a RetryAfter delay.
# Cap the wait so a throttled answer is still delivered instead of being dropped silently.
_MAX_FLOOD_WAIT_SECONDS = 40.0


class _FloodControl(Exception):
    """Telegram asked us to back off; carries how long to wait before retrying."""

    def __init__(self, wait: float) -> None:
        super().__init__(f"telegram flood control, retry in {wait:.0f}s")
        self.wait = wait


def _retry_after_seconds(exc: RetryAfter) -> float:
    """Read the RetryAfter delay as seconds, accepting int or ``timedelta`` (newer PTB)."""
    retry_after = exc.retry_after
    total_seconds = getattr(retry_after, "total_seconds", None)
    return total_seconds() if callable(total_seconds) else float(retry_after)


class TelegramStreamingReply:
    """An editable message that shows a live checklist of the steps the agent runs."""

    def __init__(
        self,
        bot: Any,
        chat_id: str | int,
        reply_to_message_id: str | int | None = None,
        message_thread_id: str | int | None = None,
        status_interval_seconds: float = 4.0,
        finalize_delay_seconds: float = 1.5,
        progress_edit_gap_seconds: float = 3.0,
        parse_mode: str = "HTML",
    ) -> None:
        self.bot = bot
        self.chat_id = chat_id
        self.reply_to_message_id = reply_to_message_id
        self.message_thread_id = message_thread_id
        self.status_interval_seconds = status_interval_seconds
        # Progress edits are coalesced: Telegram flood control drops the final answer when the
        # placeholder is edited on every tool step. New steps still update at most this often,
        # and a coalesced step is flushed by the periodic refresh.
        self.progress_edit_gap_seconds = progress_edit_gap_seconds
        self._last_progress_edit = 0.0
        self._pending_progress = False
        self.finalize_delay_seconds = finalize_delay_seconds
        self.parse_mode = parse_mode
        self.message_id: int | None = None
        self.draft_id = abs(hash(f"{chat_id}_{id(self)}")) % 2147483640 + 1
        self._steps: list[str] = [INITIAL_STATUS]
        self._lock = asyncio.Lock()
        self._disabled = False
        self._stopped = asyncio.Event()
        self._execution_task: asyncio.Task[Any] | None = None
        self._status_task: asyncio.Task[None] | None = None
        self._started_at = 0.0

    def bind_task(self, task: asyncio.Task[Any]) -> None:
        """Bind in-flight execution task to cancel it immediately upon stop request."""
        self._execution_task = task

    def stop(self) -> None:
        """Signal that message generation should stop immediately and cancel bound task."""
        self._stopped.set()
        if self._execution_task is not None and not self._execution_task.done():
            self._execution_task.cancel()

    def is_stopped(self) -> bool:
        """Return True if message generation was stopped."""
        return self._stopped.is_set()

    @property
    def disabled(self) -> bool:
        """Return True when the placeholder could not be created and edits are skipped."""
        return self._disabled

    def _int_or_none(self, value: str | int | None) -> int | None:
        if value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def _stop_markup(self) -> InlineKeyboardMarkup:
        button = InlineKeyboardButton(
            text="⏹ Stop", callback_data=f"{STOP_CALLBACK_PREFIX}:{self.draft_id}"
        )
        return InlineKeyboardMarkup([[button]])

    def _render(self, done: bool = False) -> str:
        """Render the checklist: elapsed time in the header, no misleading per-step timer.

        The clock shows how long the whole run has been going, so it keeps ticking while a
        single tool is slow. Putting it on the active step made a long tool look stuck.
        """
        elapsed = int(asyncio.get_event_loop().time() - self._started_at)
        lines = [f"<b>🧠 Progress</b> · {elapsed}s"]
        last = len(self._steps) - 1
        for index, step in enumerate(self._steps):
            if index < last or done:
                lines.append(f"✔ {step}")
            else:
                lines.append(f"⏳ {step}")
        return "\n".join(lines)

    async def start(self) -> None:
        """Send the placeholder message, threaded to the originating message."""
        if not self.bot or not hasattr(self.bot, "send_message"):
            logger.warning(
                "Streaming disabled for chat %s: bot is unavailable or cannot send messages.",
                self.chat_id,
            )
            self._disabled = True
            return
        self._started_at = asyncio.get_event_loop().time()
        kwargs: dict[str, Any] = {"chat_id": self.chat_id, "text": self._render()}
        reply_to = self._int_or_none(self.reply_to_message_id)
        thread = self._int_or_none(self.message_thread_id)
        if reply_to is not None:
            kwargs["reply_to_message_id"] = reply_to
            kwargs["allow_sending_without_reply"] = True
        if thread is not None:
            kwargs["message_thread_id"] = thread
        try:
            sent = await self.bot.send_message(
                parse_mode=self.parse_mode, reply_markup=self._stop_markup(), **kwargs
            )
            self.message_id = getattr(sent, "message_id", None)
            self._status_task = asyncio.create_task(self._status_loop())
            logger.info(
                "Streaming placeholder sent: chat=%s reply_to=%s thread=%s message_id=%s.",
                self.chat_id,
                reply_to,
                thread,
                self.message_id,
            )
        except Exception as exc:
            logger.warning(
                "Telegram streaming placeholder failed for chat %s (reply_to=%s thread=%s): %s: %s",
                self.chat_id,
                reply_to,
                thread,
                type(exc).__name__,
                exc,
            )
            self._disabled = True

    async def _status_loop(self) -> None:
        """Refresh the active step's elapsed timer until finalization."""
        try:
            while not self._stopped.is_set() and not self._disabled:
                await asyncio.sleep(self.status_interval_seconds)
                if self._stopped.is_set() or self._disabled or self.message_id is None:
                    return
                async with self._lock:
                    if self._stopped.is_set() or self._disabled:
                        return
                    now = asyncio.get_event_loop().time()
                    if (
                        not self._pending_progress
                        and now - self._last_progress_edit < self.progress_edit_gap_seconds
                    ):
                        continue
                    self._pending_progress = False
                    try:
                        await self._edit(self._render(), parse_mode=self.parse_mode)
                    except _FloodControl as flood:
                        logger.debug(
                            "Skipping progress refresh for chat %s: %s", self.chat_id, flood
                        )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.debug("Telegram status refresh stopped for chat %s: %s", self.chat_id, exc)

    async def _cancel_status(self) -> None:
        """Stop the timer refresh before it can race a final edit."""
        task = self._status_task
        self._status_task = None
        if task is None or task.done():
            return
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    async def set_status(self, text: str) -> None:
        """Append a factual step (the specialist or tool actually running) to the checklist."""
        if self._disabled or self.message_id is None or self._stopped.is_set():
            return
        if self._steps[-1] == text:
            return
        self._steps.append(text)
        now = asyncio.get_event_loop().time()
        if now - self._last_progress_edit < self.progress_edit_gap_seconds:
            # Too soon since the last edit; flush this step on the next refresh tick.
            self._pending_progress = True
            return
        async with self._lock:
            if self._stopped.is_set() or self._disabled or self.message_id is None:
                return
            try:
                await self._edit(self._render(), parse_mode=self.parse_mode)
            except _FloodControl as flood:
                logger.debug("Skipping progress edit for chat %s: %s", self.chat_id, flood)

    async def _edit(
        self, text: str, parse_mode: str | None, reply_markup: Any = "__keep__"
    ) -> bool:
        if self.message_id is None:
            return False
        markup = self._stop_markup() if reply_markup == "__keep__" else reply_markup
        kwargs: dict[str, Any] = {
            "chat_id": self.chat_id,
            "message_id": self.message_id,
            "text": text[:MAX_MESSAGE_LENGTH],
            "reply_markup": markup,
        }
        if parse_mode:
            kwargs["parse_mode"] = parse_mode
        try:
            await self.bot.edit_message_text(**kwargs)
            self._last_progress_edit = asyncio.get_event_loop().time()
            return True
        except RetryAfter as exc:
            raise _FloodControl(
                min(_retry_after_seconds(exc) + 0.5, _MAX_FLOOD_WAIT_SECONDS)
            ) from exc
        except Exception as exc:
            if "not modified" in str(exc).lower():
                return True
            logger.debug("Telegram stream edit failed for chat %s: %s", self.chat_id, exc)
            return False

    async def _edit_html_with_plain_fallback(self, text: str) -> bool:
        async def _attempt() -> bool:
            if await self._edit(text, parse_mode=self.parse_mode, reply_markup=None):
                return True
            plain = _TAG_PATTERN.sub("", text)
            return await self._edit(plain, parse_mode=None, reply_markup=None)

        try:
            result = await _attempt()
        except _FloodControl as flood:
            logger.warning(
                "Telegram flood control for chat %s; waiting %.0fs before delivering.",
                self.chat_id,
                flood.wait,
            )
            await asyncio.sleep(flood.wait)
            try:
                result = await _attempt()
            except _FloodControl:
                result = False
        if not result:
            logger.warning(
                "Streaming edit failed for chat %s message_id=%s (both HTML and plain text).",
                self.chat_id,
                self.message_id,
            )
        return result

    def _done_text(self) -> str:
        """Completed checklist plus a Done marker, kept until the answer replaces it."""
        elapsed = int(asyncio.get_event_loop().time() - self._started_at)
        return f"{self._render(done=True)}\n\n✅ <b>Done</b> · {elapsed}s"

    async def finalize(self, formatted_text: str) -> bool:
        """Mark the checklist done, then replace the whole message with the final answer.

        The operator keeps watching the completed tool list with a ``Done`` footer for a few
        seconds, after which the transient steps are swapped out for the answer.
        """
        await self._cancel_status()
        if self._disabled or self.message_id is None:
            logger.warning(
                "Cannot finalize streaming reply for chat %s: disabled=%s message_id=%s.",
                self.chat_id,
                self._disabled,
                self.message_id,
            )
            return False
        await self._edit_html_with_plain_fallback(self._done_text())
        if self.finalize_delay_seconds > 0:
            await asyncio.sleep(self.finalize_delay_seconds)
        if self._stopped.is_set():
            logger.info("Streaming reply for chat %s was stopped before delivery.", self.chat_id)
            return False
        delivered = await self._edit_html_with_plain_fallback(formatted_text)
        logger.info(
            "Streaming reply delivered for chat %s message_id=%s: %s.",
            self.chat_id,
            self.message_id,
            delivered,
        )
        return delivered

    async def finalize_stopped(self) -> None:
        """Freeze the checklist with a stopped note when the operator pressed stop."""
        await self._cancel_status()
        text = f"{self._render(done=True)}\n\n⏹ <i>Generation stopped</i>"
        if not await self._edit_html_with_plain_fallback(text):
            await self.discard()

    async def discard(self) -> None:
        """Delete the placeholder when no answer will replace it."""
        await self._cancel_status()
        if self._disabled or self.message_id is None or not self.bot:
            return
        try:
            await self.bot.delete_message(chat_id=self.chat_id, message_id=self.message_id)
            logger.info(
                "Streaming placeholder deleted: chat=%s message_id=%s.",
                self.chat_id,
                self.message_id,
            )
        except Exception as exc:
            logger.debug("Could not delete streaming placeholder in chat %s: %s", self.chat_id, exc)
