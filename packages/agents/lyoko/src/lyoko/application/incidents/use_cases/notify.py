"""Use case for formatting and broadcasting incident notification reports."""

import asyncio
import logging
from typing import Any

from langchain_core.runnables import RunnableConfig
from lyoko.application.context.formatter import IncidentContextFormatter
from lyoko.application.incidents.use_cases.enrich_trace_cost import EnrichTraceCostUseCase
from lyoko.config import settings
from lyoko.domain.interfaces.chat_service import ChatServiceInterface
from lyoko.domain.models.incident import IncidentStatus
from lyoko.domain.models.state import is_message

logger = logging.getLogger("lyoko.application.incidents.use_cases.notify")


def format_incident_report(state: dict[str, Any]) -> str:
    """Build a structured Markdown incident summary from workflow state."""
    resolved = bool(state.get("is_resolved"))
    raw_status = state.get("status")

    status_enum: IncidentStatus | None = None
    if isinstance(raw_status, IncidentStatus):
        status_enum = raw_status
    elif isinstance(raw_status, str):
        try:
            status_enum = IncidentStatus(raw_status)
        except ValueError:
            status_enum = None

    if resolved or status_enum == IncidentStatus.RESOLVED:
        status = IncidentStatus.RESOLVED.badge
    elif status_enum is not None:
        status = status_enum.badge
    else:
        status = IncidentStatus.ESCALATED.badge

    lines = ["📋 **LYOKO Incident Report**", "", f"**Status:** {status}"]
    if not is_message(state):
        labels = state.get("labels") or {}
        target = ", ".join(f"{k}={labels[k]}" for k in sorted(labels) if k != "alertname")
        lines.append(f"**Alert:** {state.get('alert_name', '')}")
        if target:
            lines.append(f"**Target:** {target}")
    lines.append(f"**Root Cause:** {state.get('root_cause', 'Unknown')}")
    lines.append(f"**Action Taken:** {state.get('action_taken', 'None')}")
    if state.get("matched_memories"):
        lines.append(f"**Memories Applied:** {len(state['matched_memories'])}")
    actions = state.get("actions") or []
    if actions:
        lines.append(
            "**Tool Calls:** "
            + ", ".join(IncidentContextFormatter.describe_call(a) for a in actions)
        )
    if state.get("verification"):
        lines.append(f"**Verification:** {state['verification']}")
    return "\n".join(lines)


class NotifyIncidentReportUseCase:
    """Formats and publishes incident reports across active channels."""

    def __init__(
        self,
        chat_manager: ChatServiceInterface | None = None,
        enrich_trace_cost: EnrichTraceCostUseCase | None = None,
    ) -> None:
        self.chat_manager = chat_manager
        self.enrich_trace_cost = enrich_trace_cost

    async def execute(
        self, state: dict[str, Any], config: RunnableConfig | None = None
    ) -> dict[str, Any]:
        """Build the incident report. Alerts broadcast it; a message gets it as the reply."""
        summary = format_incident_report(state)
        if is_message(state):
            # For messages, the chat pipeline (process_message) enriches and sends the reply.
            return {"reply": summary}

        trace_id = state.get("trace_id")
        if self.enrich_trace_cost is not None and trace_id:
            is_llm = not state.get("is_recovery", False)
            summary = await self.enrich_trace_cost.execute(
                summary,
                trace_id,
                session_id=state.get("session_id"),
                is_llm_called=is_llm,
            )

        logger.info(summary)
        if self.chat_manager is not None:
            progress_message_id = state.get("progress_message_id")
            progress_chat_id = state.get("progress_chat_id")
            target_chat_id = (
                progress_chat_id or state.get("chat_id") or settings.telegram_default_chat_id or ""
            )
            target_msg_id = progress_message_id
            edited = False
            if progress_message_id and progress_chat_id:
                results = await self.chat_manager.edit_message(
                    chat_id=progress_chat_id,
                    message_id=progress_message_id,
                    text=summary,
                )
                edited = bool(results)
            if not edited:
                sent_results = await self.chat_manager.broadcast_message(
                    chat_id=target_chat_id, text=summary, session_id=state.get("session_id")
                )
                if sent_results and sent_results[0]:
                    target_msg_id = sent_results[0].message_id
                    target_chat_id = sent_results[0].chat_id

            if (
                self.enrich_trace_cost is not None
                and trace_id
                and target_msg_id
                and target_chat_id
                and "💸 **Cost:** calculating..." in summary
            ):
                asyncio.create_task(
                    self.enrich_trace_cost.update_message_cost_async(
                        chat_manager=self.chat_manager,
                        chat_id=target_chat_id,
                        message_id=target_msg_id,
                        text_with_placeholder=summary,
                        trace_id=trace_id,
                        session_id=state.get("session_id"),
                    )
                )
        return {}
