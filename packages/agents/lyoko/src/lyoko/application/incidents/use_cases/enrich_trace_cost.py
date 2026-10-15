"""Use case for enriching messages with Langfuse trace metrics and URL footer via sector5-mcp."""

import asyncio
import logging
from typing import Any

from lyoko.application.incidents.trace_cost_service import TraceCostResult, TraceCostService
from lyoko.domain.interfaces.mcp import MCPClientInterface
from lyoko.domain.interfaces.tracer import TracerInterface

logger = logging.getLogger("lyoko.application.use_cases.enrich_trace_cost")


class EnrichTraceCostUseCase:
    """Enriches messages with Langfuse trace metrics and URL footer via sector5-mcp."""

    def __init__(
        self,
        mcp_client: MCPClientInterface | None = None,
        tracer: TracerInterface | None = None,
        enabled: bool = True,
        cost_service: TraceCostService | None = None,
    ) -> None:
        self.mcp_client = mcp_client
        self.tracer = tracer
        self.enabled = enabled
        self._cost_service = cost_service or TraceCostService()

    @staticmethod
    def _parse_cost_from_result(content: Any) -> float | None:
        return TraceCostService.parse_cost_from_result(content)

    @staticmethod
    def _format_cost(cost: float | None) -> str | None:
        return TraceCostService.format_cost(cost)

    async def _query_trace_cost(
        self, trace_id: str, session_id: str | None = None
    ) -> TraceCostResult:
        return await self._cost_service.query_trace_cost(
            self.mcp_client, trace_id, session_id=session_id
        )

    def _resolve_trace_url(
        self,
        trace_id: str,
        resolved_tid: str | None,
        resolved_url: str | None,
    ) -> str | None:
        if resolved_url:
            return resolved_url
        if resolved_tid and self.tracer is not None:
            try:
                return self.tracer.get_trace_url(resolved_tid)
            except Exception:
                pass
        if self.tracer is not None:
            try:
                return self.tracer.get_trace_url(trace_id)
            except Exception:
                pass
        return None

    async def execute(
        self,
        text: str,
        trace_id: str | None,
        session_id: str | None = None,
        is_llm_called: bool = True,
    ) -> str:
        """Append trace cost and trace link footer to text if available."""
        if not self.enabled or not trace_id or not text:
            return text

        if "[View Trace]" in text or f"/traces/{trace_id}" in text:
            return text

        if not is_llm_called:
            target_tid = TraceCostService.derive_trace_id(session_id) if session_id else trace_id
            trace_url = self._resolve_trace_url(trace_id, target_tid, None)
            footer_parts = [f"💸 **Cost:** {self._format_cost(0.0)}"]
            if trace_url:
                footer_parts.append(f"🔍 [View Trace]({trace_url})")
            return f"{text.rstrip()}\n\n{' · '.join(footer_parts)}"

        if self.tracer is not None:
            try:
                self.tracer.flush()
            except Exception as exc:
                logger.debug("Failed to flush tracer: %s", exc)

        res: TraceCostResult | None = None
        query_failed = False
        if self.mcp_client is not None:
            try:
                res = await self._query_trace_cost(trace_id, session_id=session_id)
            except Exception:
                query_failed = True

        resolved_tid = (
            res.resolved_trace_id
            if res and res.resolved_trace_id
            else (TraceCostService.derive_trace_id(session_id) if session_id else trace_id)
        )
        resolved_url = res.resolved_url if res else None
        trace_url = self._resolve_trace_url(trace_id, resolved_tid, resolved_url)

        cost_str: str | None = None
        if res and res.main_cost is not None:
            cost_str = TraceCostService.format_cost_with_breakdown(res.main_cost, res.memory_cost)
        elif self.mcp_client is not None and not query_failed:
            cost_str = "calculating..."

        footer_parts = []
        if cost_str:
            footer_parts.append(f"💸 **Cost:** {cost_str}")
        if trace_url:
            footer_parts.append(f"🔍 [View Trace]({trace_url})")

        if not footer_parts:
            return text

        footer = " · ".join(footer_parts)
        return f"{text.rstrip()}\n\n{footer}"

    def _apply_updated_trace_url(
        self, text: str, trace_id: str, new_url: str, resolved_tid: str | None
    ) -> str:
        if self.tracer is not None:
            old_url = self.tracer.get_trace_url(trace_id)
            if old_url and old_url in text:
                return text.replace(old_url, new_url)
        if f"/traces/{trace_id}" in text and resolved_tid:
            return text.replace(f"/traces/{trace_id}", f"/traces/{resolved_tid}")
        if "[View Trace]" not in text:
            return f"{text} · 🔍 [View Trace]({new_url})"
        return text

    async def update_message_cost_async(
        self,
        chat_manager: Any,
        chat_id: str,
        message_id: str | int,
        text_with_placeholder: str,
        trace_id: str,
        session_id: str | None = None,
        delay_seconds: float = 2.0,
        max_attempts: int = 5,
        poll_interval: float = 1.5,
    ) -> None:
        """Background task to query Langfuse after ingestion and update Telegram message."""
        if not self.enabled or not chat_manager or not text_with_placeholder or not trace_id:
            return
        if "💸 **Cost:** calculating..." not in text_with_placeholder:
            return

        try:
            await asyncio.sleep(delay_seconds)
            res: TraceCostResult | None = None

            for attempt in range(max_attempts):
                current_res = await self._query_trace_cost(trace_id, session_id=session_id)
                if current_res.main_cost is not None:
                    res = current_res
                    # If memory review is expected and hasn't ingested yet, give it another attempt
                    if session_id and current_res.memory_cost is None and attempt < 2:
                        await asyncio.sleep(poll_interval)
                        continue
                    break
                if attempt < max_attempts - 1:
                    await asyncio.sleep(poll_interval)

            if res is None or res.main_cost is None:
                target_tid = (
                    TraceCostService.derive_trace_id(session_id) if session_id else trace_id
                )
                try:
                    fallback = await self._cost_service.query_metrics_for_trace(
                        self.mcp_client, target_tid
                    )
                    res = TraceCostResult(
                        main_cost=fallback if fallback is not None else 0.0,
                        resolved_trace_id=target_tid,
                    )
                except Exception:
                    res = TraceCostResult(main_cost=0.0, resolved_trace_id=target_tid)

            cost_str = TraceCostService.format_cost_with_breakdown(res.main_cost, res.memory_cost)
            if not cost_str:
                return

            updated = text_with_placeholder.replace(
                "💸 **Cost:** calculating...", f"💸 **Cost:** {cost_str}"
            )
            new_url = self._resolve_trace_url(trace_id, res.resolved_trace_id, res.resolved_url)
            if new_url:
                updated = self._apply_updated_trace_url(
                    updated, trace_id, new_url, res.resolved_trace_id
                )

            sent = await chat_manager.edit_message(
                chat_id=chat_id, message_id=message_id, text=updated
            )
            is_ok = any(sent) if isinstance(sent, list) else bool(sent)
            if is_ok:
                logger.info(
                    "Updated trace cost placeholder for msg %s in chat %s to %s",
                    message_id,
                    chat_id,
                    cost_str,
                )
        except Exception as exc:
            logger.debug("Failed to asynchronously update trace cost in chat message: %s", exc)
