"""Service for querying and resolving Langfuse trace metrics via MCP gateway."""

import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from lyoko.domain.interfaces.mcp import MCPClientInterface

logger = logging.getLogger("lyoko.application.incidents.trace_cost_service")


class TraceCostService:
    """Queries and resolves trace costs and URLs from Langfuse via MCP client."""

    @staticmethod
    def parse_cost_from_result(content: Any) -> float | None:
        """Extract totalCost or sum_totalCost numeric value from Langfuse tool output."""
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
                    return val if val >= 0 else None
                except (ValueError, TypeError):
                    pass
        return None

    @staticmethod
    def format_cost(cost: float | None) -> str | None:
        """Format numeric USD cost to a friendly display string."""
        if cost is None:
            return None
        if cost >= 0.0001:
            return f"${cost:.4f}"
        if cost > 0:
            return "<$0.0001"
        return "$0.0000"

    async def query_metrics_for_trace(
        self, mcp_client: MCPClientInterface | None, trace_id: str
    ) -> float | None:
        """Query langfuse_queryMetrics tool for an exact traceId."""
        if mcp_client is None:
            return None
        now = datetime.now(UTC)
        from_iso = (now - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        to_iso = (now + timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
        resp = await mcp_client.call_tool(
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
        return self.parse_cost_from_result(raw_content)

    async def resolve_trace_from_session(
        self, mcp_client: MCPClientInterface | None, session_id: str
    ) -> tuple[str | None, str | None]:
        """Resolve the active Langfuse trace ID and clean trace URL for a session."""
        if mcp_client is None or not session_id:
            return None, None
        try:
            now = datetime.now(UTC)
            from_iso = (now - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
            to_iso = (now + timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
            resp = await mcp_client.call_tool(
                name="langfuse_listObservations",
                arguments={
                    "limit": 5,
                    "fromStartTime": from_iso,
                    "toStartTime": to_iso,
                    "fields": ["id", "traceId", "name", "type", "sessionId"],
                    "filter": [
                        {
                            "column": "sessionId",
                            "operator": "=",
                            "value": session_id,
                        }
                    ],
                },
            )
            raw_content = (
                resp.get("content") if isinstance(resp, dict) else getattr(resp, "content", resp)
            )
            data = raw_content
            if isinstance(raw_content, str):
                try:
                    data = json.loads(raw_content)
                except Exception:
                    return None, None
            if isinstance(data, dict) and "data" in data and isinstance(data["data"], list):
                for obs in data["data"]:
                    tid = obs.get("traceId")
                    if tid:
                        raw_url = obs.get("url")
                        clean_url = raw_url.split("?")[0] if raw_url else None
                        return tid, clean_url
        except Exception as exc:
            logger.debug("Failed to resolve trace from session %s: %s", session_id, exc)
        return None, None

    async def query_trace_cost(
        self,
        mcp_client: MCPClientInterface | None,
        trace_id: str,
        session_id: str | None = None,
    ) -> tuple[float | None, str | None, str | None]:
        """Query Langfuse for execution cost and resolved trace info."""
        query_exc = None
        try:
            cost = await self.query_metrics_for_trace(mcp_client, trace_id)
            if cost is not None and cost > 0:
                return cost, trace_id, None
        except Exception as exc:
            query_exc = exc
            logger.debug("Direct traceId cost query failed: %s", exc)

        if session_id:
            resolved_tid, resolved_url = await self.resolve_trace_from_session(
                mcp_client, session_id
            )
            if resolved_tid:
                try:
                    resolved_cost = await self.query_metrics_for_trace(mcp_client, resolved_tid)
                    if resolved_cost is not None:
                        return resolved_cost, resolved_tid, resolved_url
                except Exception as exc:
                    query_exc = exc
                    logger.debug("Resolved traceId cost query failed: %s", exc)
                return None, resolved_tid, resolved_url

        if query_exc is not None:
            raise query_exc

        return None, trace_id, None
