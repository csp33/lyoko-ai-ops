"""Telegram HTTP Client Adapter implementation."""

import logging
from typing import Any

import httpx
from pydantic import SecretStr

from sector5_mcp.domain.interfaces.telegram import TelegramClientInterface
from sector5_mcp.domain.models.telegram import (
    TelegramAlertRequest,
    TelegramMessageRequest,
    TelegramReactionRequest,
    TelegramSeverity,
)

logger = logging.getLogger("sector5_mcp.telegram")

SEVERITY_ICONS: dict[TelegramSeverity, str] = {
    TelegramSeverity.INFO: "ℹ️",
    TelegramSeverity.WARNING: "⚠️",
    TelegramSeverity.CRITICAL: "🚨",
    TelegramSeverity.OK: "✅",
}


class TelegramClient(TelegramClientInterface):
    """Asynchronous HTTP client for interacting with the Telegram Bot API."""

    def __init__(
        self,
        bot_token: str | SecretStr | None,
        default_chat_id: str | None = None,
    ) -> None:
        self.bot_token = (
            bot_token.get_secret_value() if isinstance(bot_token, SecretStr) else (bot_token or "")
        )
        self.default_chat_id = default_chat_id
        self.base_url = f"https://api.telegram.org/bot{self.bot_token}"

    async def send_message(self, request: TelegramMessageRequest) -> dict[str, Any]:
        """Send a markdown text message to Telegram."""
        chat_id = request.chat_id or self.default_chat_id
        if not chat_id:
            raise ValueError("No chat_id specified and no default_chat_id configured")
        if not self.bot_token:
            raise ValueError("Telegram bot token not configured")

        url = f"{self.base_url}/sendMessage"
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "text": request.text,
            "parse_mode": request.parse_mode,
        }
        if request.reply_to_message_id is not None:
            payload["reply_parameters"] = {"message_id": int(request.reply_to_message_id)}

        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            return resp.json()

    async def set_reaction(self, request: TelegramReactionRequest) -> dict[str, Any]:
        """Set an emoji reaction on a message in Telegram."""
        chat_id = request.chat_id or self.default_chat_id
        if not chat_id:
            raise ValueError("No chat_id specified and no default_chat_id configured")
        if not self.bot_token:
            raise ValueError("Telegram bot token not configured")

        url = f"{self.base_url}/setMessageReaction"
        payload = {
            "chat_id": chat_id,
            "message_id": int(request.message_id),
            "reaction": [{"type": "emoji", "emoji": request.emoji}],
        }
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            return resp.json()

    async def send_alert(self, request: TelegramAlertRequest) -> dict[str, Any]:
        """Format and send an alert notification to Telegram."""
        icon = SEVERITY_ICONS.get(request.severity, "ℹ️")
        formatted_text = (
            f"{icon} *[{request.severity.value.upper()}] {request.title}*\n\n{request.message}"
        )
        msg_req = TelegramMessageRequest(
            text=formatted_text,
            chat_id=request.chat_id or self.default_chat_id,
            parse_mode="Markdown",
        )
        return await self.send_message(msg_req)
