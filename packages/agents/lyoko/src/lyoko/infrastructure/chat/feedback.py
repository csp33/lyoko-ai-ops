"""Feedback command handling for the Telegram chat connector.

Turns a /feedback or /teach operator instruction into a persisted memory entry, extracting the
incident context from the message being replied to when present.
"""

import html
import logging
from typing import Any

from lyoko.domain.interfaces.embeddings import EmbeddingsServiceInterface
from lyoko.domain.interfaces.memory import MemoryRepositoryInterface

logger = logging.getLogger("lyoko.chat.telegram")


class TelegramFeedbackMixin:
    """Persist operator feedback and taught rules into semantic memory."""

    memory_repository: MemoryRepositoryInterface | None
    embeddings_service: EmbeddingsServiceInterface | None

    async def _handle_feedback_command(self, msg: Any, text: str) -> None:
        """Process /feedback or /teach operator instruction and persist to PostgreSQL."""
        message_thread_id = getattr(msg, "message_thread_id", None)
        feedback_content = text.removeprefix("/feedback").removeprefix("/teach").strip()
        if not feedback_content:
            help_text = (
                "ℹ️ <b>Feedback usage:</b>\n"
                "<code>/feedback &lt;rule or instruction for LYOKO&gt;</code>\n\n"
                "<i>Example:</i> <code>/feedback For influxdb do not increase RAM, compact logs first</code>\n"
                "<i>Tip:</i> Reply to any incident report with <code>/feedback &lt;rule&gt;</code> to automatically associate it with that pod/service."
            )
            await msg.reply_text(
                help_text,
                parse_mode="HTML",
                reply_to_message_id=msg.message_id,
                message_thread_id=message_thread_id,
                allow_sending_without_reply=True,
            )
            return

        if not self.memory_repository:
            await msg.reply_text(
                "⚠️ The persistent memory database (PostgreSQL) is not available.",
                reply_to_message_id=msg.message_id,
                message_thread_id=message_thread_id,
                allow_sending_without_reply=True,
            )
            return

        from lyoko.application.memory.use_cases.feedback import RecordFeedbackUseCase

        use_case = RecordFeedbackUseCase(
            memory_repository=self.memory_repository,
            embeddings_service=self.embeddings_service,
        )

        reply_to = getattr(msg, "reply_to_message", None)
        reply_to_text = getattr(reply_to, "text", None) if reply_to else None

        memory_id, service_name, namespace = await use_case.execute_from_chat_command(
            feedback_content=feedback_content,
            reply_to_text=reply_to_text,
        )

        success_msg = (
            f"🧠 <b>Rule learned and stored in PostgreSQL</b> (ID: <code>{memory_id}</code>)\n\n"
            f"• <b>Service:</b> <code>{html.escape(service_name)}</code> ({html.escape(namespace)})\n"
            f"• <b>Rule:</b> <i>{html.escape(feedback_content)}</i>\n\n"
            f"LYOKO will apply this semantic guideline in future incidents."
        )
        await msg.reply_text(
            success_msg,
            parse_mode="HTML",
            reply_to_message_id=msg.message_id,
            message_thread_id=message_thread_id,
            allow_sending_without_reply=True,
        )

    async def _handle_forget_command(self, msg: Any, text: str) -> None:
        """Process /forget <id> operator instruction and delete rule from PostgreSQL."""
        message_thread_id = getattr(msg, "message_thread_id", None)
        if not self.memory_repository:
            await msg.reply_text(
                "⚠️ The persistent memory database (PostgreSQL) is not available.",
                reply_to_message_id=msg.message_id,
                message_thread_id=message_thread_id,
                allow_sending_without_reply=True,
            )
            return

        parts = text.strip().split()
        if len(parts) < 2:
            recent = await self.memory_repository.list_recent_memories(limit=5)
            lines = [
                "ℹ️ <b>Forget usage:</b>\n"
                "<code>/forget &lt;id&gt;</code>\n\n"
                "<i>Example:</i> <code>/forget 42</code>\n"
                "<i>Tip:</i> Run <code>/memories</code> to view stored memories and their IDs."
            ]
            if recent:
                lines.append("\n🧠 <b>Recent memories:</b>")
                for m in recent:
                    rule = html.escape(m.action_rule or m.operator_feedback or "")
                    svc = html.escape(f"{m.namespace} / {m.service_name}")
                    lines.append(f"• <b>[#{m.id}]</b> <code>{svc}</code>: <i>{rule}</i>")
            await msg.reply_text(
                "\n".join(lines),
                parse_mode="HTML",
                reply_to_message_id=msg.message_id,
                message_thread_id=message_thread_id,
                allow_sending_without_reply=True,
            )
            return

        raw_id = parts[1].lstrip("#")
        if not raw_id.isdigit():
            await msg.reply_text(
                "⚠️ <b>Invalid memory ID.</b> Usage: <code>/forget &lt;id&gt;</code> (ID must be a number).\n"
                "Run <code>/memories</code> to see active memory IDs.",
                parse_mode="HTML",
                reply_to_message_id=msg.message_id,
                message_thread_id=message_thread_id,
                allow_sending_without_reply=True,
            )
            return

        memory_id = int(raw_id)
        deleted = await self.memory_repository.delete_memory(memory_id)
        if deleted:
            reply_text = f"🗑️ <b>Memory #{memory_id} deleted successfully.</b>"
        else:
            reply_text = (
                f"⚠️ <b>Memory #{memory_id} not found.</b> "
                "Run <code>/memories</code> to see active memory IDs."
            )

        await msg.reply_text(
            reply_text,
            parse_mode="HTML",
            reply_to_message_id=msg.message_id,
            message_thread_id=message_thread_id,
            allow_sending_without_reply=True,
        )

    async def _handle_memories_command(self, msg: Any, _text: str) -> None:
        """Process /memories command and list recent memories."""
        message_thread_id = getattr(msg, "message_thread_id", None)
        if not self.memory_repository:
            await msg.reply_text(
                "⚠️ The persistent memory database (PostgreSQL) is not available.",
                reply_to_message_id=msg.message_id,
                message_thread_id=message_thread_id,
                allow_sending_without_reply=True,
            )
            return

        memories = await self.memory_repository.list_recent_memories(limit=10)
        if not memories:
            await msg.reply_text(
                "🧠 <b>No memories found in database.</b>",
                parse_mode="HTML",
                reply_to_message_id=msg.message_id,
                message_thread_id=message_thread_id,
                allow_sending_without_reply=True,
            )
            return

        lines = ["🧠 <b>Recent Stored Rules & Memories:</b>\n"]
        for m in memories:
            rule = html.escape(m.action_rule or m.operator_feedback or "")
            svc = html.escape(f"{m.namespace} / {m.service_name}")
            lines.append(f"• <b>[#{m.id}]</b> <code>{svc}</code>: <i>{rule}</i>")
        lines.append("\n💡 <i>To delete a rule:</i> <code>/forget &lt;id&gt;</code>")

        await msg.reply_text(
            "\n".join(lines),
            parse_mode="HTML",
            reply_to_message_id=msg.message_id,
            message_thread_id=message_thread_id,
            allow_sending_without_reply=True,
        )
