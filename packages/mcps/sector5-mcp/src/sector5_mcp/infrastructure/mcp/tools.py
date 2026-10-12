"""Gateway MCP tool handlers for domain discovery and execution."""

import json
from typing import Any

from fastmcp import FastMCP

from sector5_mcp.application.service import MCPGatewayService
from sector5_mcp.domain.models.upstream import UpstreamType
from sector5_mcp.infrastructure.mcp.formatting import shape_tool_content, tool_index

_MAX_DOMAIN_PAGE = 100

# Markers an upstream uses to reject a call for its arguments (e.g. a 422). When one appears, the
# gateway attaches the tool's real schema so the agent can self-correct without a second lookup.
_ARGUMENT_ERROR_MARKERS = (
    "unknown argument",
    "required argument",
    "missing required",
    "invalid argument",
    "invalid type",
    "unexpected keyword",
    "422",
    "unprocessable",
)


def _is_argument_error(message: str) -> bool:
    lowered = message.lower()
    return any(marker in lowered for marker in _ARGUMENT_ERROR_MARKERS)


def register_gateway_tools(mcp: FastMCP, service: MCPGatewayService) -> None:
    """Register the domain discovery and tool execution handlers on the MCP server."""

    @mcp.tool()
    async def gateway_get_domain_tools(
        domain: UpstreamType,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Return one page of the tool index for one upstream domain.

        Each entry carries the tool name, a one-line description, and its full JSON parameter
        schema, so the exact argument names are always visible and never guessed. Page with
        ``limit``/``offset`` and follow ``has_more``. Execute with gateway_call_tool.

        Args:
            domain: Upstream domain (e.g. kubernetes, unifi, homeassistant, grafana, github, telegram).
            limit: Maximum tools to return in this page (default 50, max 100).
            offset: Number of tools to skip for pagination (default 0).
        """
        tools = await service.get_domain_tools(domain)
        total = len(tools)
        if total == 0:
            return {
                "error": (
                    f"No tools for domain '{str(domain)}'. "
                    "It may be disabled, empty, or not configured."
                )
            }
        bounded_limit = max(1, min(limit, _MAX_DOMAIN_PAGE))
        bounded_offset = max(0, offset)
        page = tools[bounded_offset : bounded_offset + bounded_limit]
        return {
            "domain": str(domain),
            "total": total,
            "limit": bounded_limit,
            "offset": bounded_offset,
            "has_more": bounded_offset + len(page) < total,
            "tools": tool_index(page),
        }

    @mcp.tool()
    async def gateway_get_tool_schema(tool_name: str) -> dict[str, Any]:
        """Get the full parameter schema and detailed description for a specific operational tool.

        Args:
            tool_name: The exact name of the tool to inspect.
        """
        definition = await service.get_tool_definition(tool_name)
        if definition is None:
            return {"error": f"Tool '{tool_name}' not found."}
        return {
            "name": definition.name,
            "description": definition.description,
            "upstream": str(definition.upstream_type),
            "parameters": definition.parameters,
            "annotations": definition.annotations,
            "read_only_hint": definition.read_only,
        }

    @mcp.tool()
    async def gateway_call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Execute any tool across the connected upstream MCP servers with authentication and guardrails.

        Args:
            tool_name: Name of the upstream tool to invoke.
            arguments: Dictionary of arguments matching the tool schema.
        """
        result = await service.execute_tool(tool_name, arguments)
        content = shape_tool_content(result.content, result.is_error)
        if result.is_error and _is_argument_error(str(result.content)):
            definition = await service.get_tool_definition(tool_name)
            if definition is not None:
                content = (
                    f"{content}\n\n[The call was rejected for its arguments. The correct parameter "
                    f"schema for '{definition.name}' is:\n"
                    f"{json.dumps(definition.parameters)}\n"
                    "Retry once with these exact argument names.]"
                )
        return {
            "status": result.status,
            "content": content,
            "is_error": result.is_error,
        }
