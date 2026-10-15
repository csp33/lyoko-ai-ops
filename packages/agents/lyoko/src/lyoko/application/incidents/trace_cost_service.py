"""Service for querying and resolving Langfuse trace metrics via MCP gateway."""

import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any, NamedTuple

from lyoko.domain.interfaces.mcp import MCPClientInterface

logger = logging.getLogger("lyoko.application.incidents.trace_cost_service")


class TraceCostResult(NamedTuple):
    """Execution cost metrics with optional memory breakdown and resolved trace URL."""

    main_cost: float | None = None
    memory_cost: float | None = None
    resolved_trace_id: str | None = None
    resolved_url: str | None = None


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

    @classmethod
    def format_cost_with_breakdown(
        cls, main_cost: float | None, memory_cost: float | None = None
    ) -> str | None:
        """Format primary execution cost with optional memory review breakdown."""
        if main_cost is None:
            return None
        main_str = cls.format_cost(main_cost)
        if not main_str:
            return None
        if memory_cost is not None and memory_cost > 0:
            mem_str = cls.format_cost(memory_cost)
            if mem_str:
                return f"{main_str} (+{mem_str} mem)"
        return main_str

    @staticmethod
    def derive_trace_id(session_id: str) -> str:
        """Derive a deterministic 32-character hex trace ID from a session ID matching Hermes."""
        seed = f"{session_id}::{session_id}"
        import hashlib

        return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:32]

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

    async def resolve_session_traces(
        self, mcp_client: MCPClientInterface | None, session_id: str
    ) -> tuple[str | None, str | None, list[str]]:
        """Resolve primary trace ID, clean URL, and secondary memory trace IDs for a session."""
        if mcp_client is None or not session_id:
            return None, None, []
        primary_tid = self.derive_trace_id(session_id)
        resolved_primary = primary_tid
        primary_url = None
        memory_tids: list[str] = []
        try:
            now = datetime.now(UTC)
            from_iso = (now - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
            to_iso = (now + timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
            resp = await mcp_client.call_tool(
                name="langfuse_listObservations",
                arguments={
                    "limit": 20,
                    "fromStartTime": from_iso,
                    "toStartTime": to_iso,
                    "fields": ["id", "traceId", "name", "type", "sessionId"],
                    "filter": [{"column": "sessionId", "operator": "=", "value": session_id}],
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
                    return resolved_primary, None, []
            if isinstance(data, dict) and "data" in data and isinstance(data["data"], list):
                project_base: str | None = None
                has_primary_in_data = False
                candidate_turns: list[tuple[str, str | None]] = []
                seen_tids: set[str] = set()

                for obs in data["data"]:
                    tid = obs.get("traceId")
                    if not tid:
                        continue
                    clean_url = (obs.get("url") or "").split("?")[0] or None
                    if clean_url and "/traces/" in clean_url and not project_base:
                        project_base = clean_url.rsplit("/traces/", 1)[0]
                    if tid == primary_tid:
                        has_primary_in_data = True
                        if clean_url and not primary_url:
                            primary_url = clean_url
                    elif (
                        obs.get("name") == "Hermes turn" or obs.get("type") in ("CHAIN", "AGENT")
                    ) and tid not in seen_tids:
                        seen_tids.add(tid)
                        candidate_turns.append((tid, clean_url))

                if has_primary_in_data:
                    memory_tids.extend(tid for tid, _ in candidate_turns)
                elif candidate_turns:
                    main_turn = (
                        candidate_turns.pop(-1)
                        if len(candidate_turns) > 1
                        else candidate_turns.pop(0)
                    )
                    resolved_primary, primary_url = main_turn[0], main_turn[1] or primary_url
                    memory_tids.extend(tid for tid, _ in candidate_turns)

                if not primary_url and project_base and resolved_primary:
                    primary_url = f"{project_base}/traces/{resolved_primary}"
        except Exception as exc:
            logger.debug("Failed to resolve session traces for %s: %s", session_id, exc)
        return resolved_primary, primary_url, memory_tids

    async def resolve_trace_from_session(
        self, mcp_client: MCPClientInterface | None, session_id: str
    ) -> tuple[str | None, str | None]:
        """Resolve the active Langfuse trace ID and clean trace URL for a session."""
        primary, url, _ = await self.resolve_session_traces(mcp_client, session_id)
        return primary, url

    async def query_trace_cost(
        self,
        mcp_client: MCPClientInterface | None,
        trace_id: str,
        session_id: str | None = None,
    ) -> TraceCostResult:
        """Query Langfuse for execution cost, optional memory cost, and resolved trace info."""
        if not session_id:
            query_exc = None
            try:
                cost = await self.query_metrics_for_trace(mcp_client, trace_id)
                if cost is not None and cost > 0:
                    return TraceCostResult(main_cost=cost, resolved_trace_id=trace_id)
            except Exception as exc:
                query_exc = exc
                logger.debug("Direct traceId cost query for %s failed: %s", trace_id, exc)
            if query_exc is not None:
                raise query_exc
            return TraceCostResult(resolved_trace_id=trace_id)

        target_tid, resolved_url, memory_tids = await self.resolve_session_traces(
            mcp_client, session_id
        )
        resolved_primary = target_tid or self.derive_trace_id(session_id)
        query_exc = None
        main_cost: float | None = None
        try:
            cost = await self.query_metrics_for_trace(mcp_client, resolved_primary)
            if cost is not None and cost > 0.0001:
                main_cost = cost
        except Exception as exc:
            query_exc = exc
            logger.debug("Direct traceId cost query for %s failed: %s", resolved_primary, exc)

        memory_cost: float | None = None
        if memory_tids:
            mem_costs: list[float] = []
            for mem_tid in memory_tids:
                try:
                    c = await self.query_metrics_for_trace(mcp_client, mem_tid)
                    if c is not None and c > 0:
                        mem_costs.append(c)
                except Exception as exc:
                    logger.debug("Memory trace cost query for %s failed: %s", mem_tid, exc)
            if mem_costs:
                memory_cost = sum(mem_costs)

        if main_cost is not None:
            return TraceCostResult(
                main_cost=main_cost,
                memory_cost=memory_cost,
                resolved_trace_id=resolved_primary,
                resolved_url=resolved_url,
            )

        if resolved_primary != trace_id:
            try:
                fallback_cost = await self.query_metrics_for_trace(mcp_client, trace_id)
                if fallback_cost is not None and fallback_cost > 0.0001:
                    return TraceCostResult(
                        main_cost=fallback_cost,
                        memory_cost=memory_cost,
                        resolved_trace_id=trace_id,
                        resolved_url=resolved_url,
                    )
            except Exception as exc:
                query_exc = exc
                logger.debug("Fallback traceId cost query for %s failed: %s", trace_id, exc)

        if query_exc is not None:
            raise query_exc

        return TraceCostResult(
            main_cost=None,
            memory_cost=memory_cost,
            resolved_trace_id=resolved_primary,
            resolved_url=resolved_url,
        )
