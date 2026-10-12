"""Tool registry managing upstream tool discovery, catalog caching, and domain lookup."""

import asyncio
import logging

from sector5_mcp.application.guardrail import GuardrailEngine
from sector5_mcp.domain.interfaces.upstream import UpstreamMCPInterface
from sector5_mcp.domain.models.aliases import resolve_canonical_domain, resolve_tool_alias
from sector5_mcp.domain.models.upstream import ToolDefinition

logger = logging.getLogger("sector5_mcp.tool_registry")


class ToolRegistry:
    """Registry coordinating tool discovery, cached routing tables, and catalog queries."""

    def __init__(
        self,
        upstreams: dict[str, UpstreamMCPInterface],
        guardrail: GuardrailEngine,
    ):
        self.upstreams = upstreams
        self.guardrail = guardrail
        self._tool_routing: dict[str, tuple[str, UpstreamMCPInterface]] = {}
        self._cached_tools: list[ToolDefinition] | None = None

    async def discover_tools(self, force_refresh: bool = False) -> list[ToolDefinition]:
        """Query all upstream MCP servers in parallel and build the aggregated tool routing table."""
        if self._cached_tools is not None and not force_refresh:
            return self._cached_tools

        aggregated_tools: list[ToolDefinition] = []
        routing: dict[str, tuple[str, UpstreamMCPInterface]] = {}

        async def fetch_upstream(name: str, client: UpstreamMCPInterface):
            try:
                tools = await client.list_tools()
                return name, client, tools
            except Exception as exc:
                logger.warning(f"Failed to discover tools from upstream '{name}': {exc}")
                return name, client, []

        results = await asyncio.gather(
            *[fetch_upstream(name, client) for name, client in self.upstreams.items()]
        )

        for upstream_name, upstream_port, tools in results:
            allowed_count = 0
            for t in tools:
                if self.guardrail.is_tool_allowed(t.name):
                    routing[t.name] = (str(upstream_name), upstream_port)
                    aggregated_tools.append(t)
                    allowed_count += 1

            logger.info(
                f"Discovered {len(tools)} tools from upstream '{upstream_name}' (allowed: {allowed_count})."
            )

        self._tool_routing = routing
        self._cached_tools = aggregated_tools
        return aggregated_tools

    def resolve_tool_routing(self, name: str) -> tuple[str, str, UpstreamMCPInterface] | None:
        """Resolve a tool name, normalized name, or alias to (resolved_name, upstream_name, client)."""
        candidates = [name]
        # Normalize dot and colon notation (e.g. unifi.get_devices -> unifi_get_devices)
        normalized = name.replace(".", "_").replace(":", "_")
        if normalized != name:
            candidates.append(normalized)

        for cand in candidates:
            if cand in self._tool_routing:
                return cand, self._tool_routing[cand][0], self._tool_routing[cand][1]
            if "." in cand:
                _, base_name = cand.split(".", 1)
                if base_name in self._tool_routing:
                    return (
                        base_name,
                        self._tool_routing[base_name][0],
                        self._tool_routing[base_name][1],
                    )
            alias = resolve_tool_alias(cand)
            if alias != cand and alias in self._tool_routing:
                return alias, self._tool_routing[alias][0], self._tool_routing[alias][1]

        return None

    def suggest_tool_names(self, name: str, n: int = 3) -> list[str]:
        """Find closest matching registered tool names for a mistyped or hallucinated tool name."""
        import difflib

        all_names = list(self._tool_routing.keys())
        if not all_names:
            return []
        # Try both the original and normalized name
        normalized = name.replace(".", "_").replace(":", "_")
        matches = difflib.get_close_matches(normalized, all_names, n=n, cutoff=0.45)
        if not matches and normalized != name:
            matches = difflib.get_close_matches(name, all_names, n=n, cutoff=0.45)
        return matches

    async def get_domain_tools(self, domain: str) -> list[ToolDefinition]:
        """Retrieve all allowed tools belonging to a specific upstream domain."""
        tools = await self.discover_tools()
        canonical_target = resolve_canonical_domain(domain)
        return [
            t
            for t in tools
            if canonical_target == str(t.upstream_type).lower()
            or canonical_target in str(t.upstream_type).lower()
            or canonical_target in t.name.lower()
        ]
