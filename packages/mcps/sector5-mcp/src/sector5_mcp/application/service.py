"""MCP Gateway Application Service coordinating upstream MCP routing, discovery, and guardrails."""

import logging
from typing import Any

from sector5_mcp.application.guardrail import GuardrailEngine
from sector5_mcp.application.registry import ToolRegistry
from sector5_mcp.application.use_cases.discover_tools import DiscoverToolsUseCase
from sector5_mcp.application.use_cases.execute_tool import ExecuteToolUseCase
from sector5_mcp.config import settings
from sector5_mcp.domain.interfaces.auth import AuthVerifierInterface
from sector5_mcp.domain.interfaces.upstream import UpstreamMCPInterface
from sector5_mcp.domain.models.auth import AuthIdentity
from sector5_mcp.domain.models.guardrail import GuardrailPolicy
from sector5_mcp.domain.models.upstream import ToolDefinition, ToolResult

logger = logging.getLogger("sector5_mcp.gateway_service")


class MCPGatewayService:
    """Core application service managing upstream MCP routing and security guardrails."""

    def __init__(
        self,
        upstreams: dict[str, UpstreamMCPInterface],
        auth_port: AuthVerifierInterface,
        guardrail_engine: GuardrailEngine | None = None,
        tool_registry: ToolRegistry | None = None,
    ):
        self.upstreams = upstreams
        self.auth = auth_port
        self.guardrail = guardrail_engine or GuardrailEngine(
            GuardrailPolicy(
                allowed_tools=settings.allowed_tools,
                blocked_tools=settings.blocked_tools,
                allowed_exec_commands=settings.allowed_exec_commands,
                blocked_exec_patterns=settings.blocked_exec_patterns,
                blocked_namespaces=settings.blocked_namespaces,
                allowed_github_repos=settings.github_allowed_repos,
                blocked_github_repos=settings.github_blocked_repos,
            )
        )
        self.registry = tool_registry or ToolRegistry(
            upstreams=self.upstreams,
            guardrail=self.guardrail,
        )
        self._discover_use_case = DiscoverToolsUseCase(registry=self.registry)
        self._execute_use_case = ExecuteToolUseCase(
            registry=self.registry,
            guardrail=self.guardrail,
        )

    def verify_access(self, token: str | None) -> AuthIdentity:
        """Verify client authentication credentials via the injected auth port."""
        return self.auth.verify(token)

    async def discover_tools(self, force_refresh: bool = False) -> list[ToolDefinition]:
        """Discover tools across all upstream MCP servers."""
        return await self._discover_use_case.execute_all(force_refresh=force_refresh)

    async def get_domain_tools(self, domain: str) -> list[ToolDefinition]:
        """Retrieve all allowed tools belonging to a specific upstream domain."""
        return await self._discover_use_case.execute_for_domain(domain)

    async def get_tool_definition(self, name: str) -> ToolDefinition | None:
        """Return one tool's definition (with its parameter schema) by name, or None."""
        return await self._discover_use_case.execute_get_definition(name)

    def _resolve_tool_routing(self, name: str) -> tuple[str, str, UpstreamMCPInterface] | None:
        """Resolve a tool name to its registered upstream handler."""
        return self.registry.resolve_tool_routing(name)

    async def execute_tool(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        """Validate safety guardrails and route tool execution to the upstream MCP server."""
        return await self._execute_use_case.execute(name, arguments)
