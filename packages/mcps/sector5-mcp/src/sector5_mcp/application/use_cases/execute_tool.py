"""Use case for validating guardrails and executing tools on upstream MCP servers."""

import logging
from typing import Any

from sector5_mcp.application.guardrail import GuardrailEngine
from sector5_mcp.application.registry import ToolRegistry
from sector5_mcp.domain.exceptions.tool import ToolNotFoundError
from sector5_mcp.domain.models.upstream import ToolResult

logger = logging.getLogger("sector5_mcp.application.use_cases.execute_tool")


class ExecuteToolUseCase:
    """Validates guardrails and executes a tool on the resolved upstream MCP server."""

    def __init__(self, registry: ToolRegistry, guardrail: GuardrailEngine) -> None:
        self.registry = registry
        self.guardrail = guardrail

    async def execute(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        """Validate safety guardrails and route tool execution to the upstream MCP server."""
        route = self.registry.resolve_tool_routing(name)
        if route is None:
            # Re-attempt discovery in case tools were registered dynamically
            await self.registry.discover_tools()
            route = self.registry.resolve_tool_routing(name)

        if route is None:
            suggestions = []
            if hasattr(self.registry, "suggest_tool_names"):
                suggestions = self.registry.suggest_tool_names(name)
            msg = f"Tool '{name}' not found on any upstream MCP server or blocked by policy."
            if suggestions:
                msg += f" Did you mean: {', '.join(suggestions)}?"
            raise ToolNotFoundError(msg)

        if len(route) == 3:
            resolved_name, upstream_name, upstream_client = route
        else:
            resolved_name = name
            upstream_name, upstream_client = route

        arguments = self._normalize_arguments(resolved_name, arguments)

        # Enforce all active security guardrails (namespace, command exec whitelist/blacklist, tool rules)
        self.guardrail.validate_tool_call(resolved_name, arguments, upstream_name=upstream_name)

        logger.info(
            f"Routing tool '{resolved_name}' (requested as '{name}') to upstream '{upstream_name}'..."
        )
        return await upstream_client.call_tool(resolved_name, arguments)

    @staticmethod
    def _normalize_arguments(tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
        """Normalize commonly confused parameters for upstream tools."""
        normalized = dict(args)
        if tool_name == "grafana_query_prometheus":
            if "expr" not in normalized and "query" in normalized:
                normalized["expr"] = normalized.pop("query")
            normalized.setdefault("datasourceUid", "prometheus")
            normalized.setdefault("endTime", "now")
            if "queryType" not in normalized:
                normalized["queryType"] = "range" if "startTime" in normalized else "instant"
            if "stepSeconds" not in normalized and "step" in normalized:
                raw_step = normalized.pop("step")
                try:
                    if isinstance(raw_step, str):
                        clean = raw_step.strip().lower()
                        if clean.endswith("s"):
                            normalized["stepSeconds"] = int(clean[:-1])
                        elif clean.endswith("m"):
                            normalized["stepSeconds"] = int(clean[:-1]) * 60
                        else:
                            normalized["stepSeconds"] = int(clean)
                    else:
                        normalized["stepSeconds"] = int(raw_step)
                except (ValueError, TypeError):
                    pass
        elif tool_name == "grafana_api_request":
            if "endpoint" not in normalized:
                if "url" in normalized:
                    normalized["endpoint"] = normalized.pop("url")
                elif "path" in normalized:
                    normalized["endpoint"] = normalized.pop("path")
            normalized.setdefault("method", "GET")
        return normalized
