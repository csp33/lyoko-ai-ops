"""Use case for enriching messages with Langfuse trace metrics and URL footer via sector5-mcp."""

import asyncio
import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

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
    ) -> None:
        self.mcp_client = mcp_client
        self.tracer = tracer
        self.enabled = enabled

    @staticmethod
    def _parse_cost_from_result(content: Any) -> float | None:
        if not content:
            return None
        data = content
        if isinstance(content, str):
            try:
                data = json.loads(content)
            except Exception:
                return None
        if isinstance(data, dict) and "data" in data and isinstance(data["data"], (list, dict)):
            data = data["data"]
        if isinstance(data, list) and data:
            data = data[0]
        if isinstance(data, dict):
            raw_val = data.get("sum_totalCost") or data.get("totalCost")
            if raw_val is not None:
                try:
                    val = float(raw_val)
                    return val if val > 0 else None
                except (ValueError, TypeError):
                    pass
        return None

    async def _query_trace_cost(self, trace_id: str) -> float | None:
        if self.mcp_client is None:
            return None
        try:
            now = datetime.now(UTC)
            from_iso = (now - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
            to_iso = (now + timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
            resp = await self.mcp_client.call_tool(
                name="langfuse_queryMetrics",
                arguments={
                    "view": "observations",
                    "dimensions": [{"field": "traceId"}],
                    "metrics": [{"measure": "totalCost", "aggregation": "sum"}],
                    "filters": [
                        {
                            "column": "traceId",
                            "operator": "=",
                            "type": "string",
                            "value": trace_id,
                        }
                    ],
                    "orderBy": [{"field": "totalCost", "direction": "desc"}],
                    "config": {"row_limit": 10},
                    "fromTimestamp": from_iso,
                    "toTimestamp": to_iso,
                },
            )
            raw_content = (
                resp.get("content") if isinstance(resp, dict) else getattr(resp, "content", resp)
            )
            return self._parse_cost_from_result(raw_content)
        except Exception as exc:
            logger.debug("Failed to query trace cost from MCP gateway: %s", exc)
            return None

    async def execute(self, text: str, trace_id: str | None) -> str:
        """Append trace cost and trace link footer to text if available."""
        if not self.enabled or not trace_id or not text:
            return text

        if "[View Trace]" in text or f"/traces/{trace_id}" in text:
            return text

        if self.tracer is not None:
            try:
                self.tracer.flush()
            except Exception as exc:
                logger.debug("Failed to flush tracer: %s", exc)

        cost = await self._query_trace_cost(trace_id)
        if cost is None and self.mcp_client is not None:
            # Langfuse computes token metrics asynchronously; wait briefly if ClickHouse ingestion lags.
            await asyncio.sleep(0.8)
            cost = await self._query_trace_cost(trace_id)

        trace_url: str | None = None
        if self.tracer is not None:
            try:
                trace_url = self.tracer.get_trace_url(trace_id)
            except Exception as exc:
                logger.debug("Failed to get trace URL: %s", exc)

        cost_str: str | None = None
        if cost is not None:
            if cost >= 0.0001:
                cost_str = f"${cost:.4f}"
            elif cost > 0:
                cost_str = "<$0.0001"
            else:
                cost_str = "$0.0000"

        footer_parts: list[str] = []
        if cost_str:
            footer_parts.append(f"💸 **Cost:** {cost_str}")
        if trace_url:
            footer_parts.append(f"🔍 [View Trace]({trace_url})")

        if not footer_parts:
            return text

        footer = " · ".join(footer_parts)
        return f"{text.rstrip()}\n\n{footer}"
