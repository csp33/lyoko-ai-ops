"""MCP client interface definitions for LYOKO."""

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from typing import Any

ToolAuthorizer = Callable[[str, dict[str, Any]], Awaitable[str | None]]
"""Decides whether an agent may call a gateway tool.

Receives the tool name and its arguments. Returns ``None`` to allow the call, or a
human-readable refusal that is shown to the agent instead of running the tool.
"""


class MCPClientInterface(ABC):
    @abstractmethod
    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        """Execute a tool on the MCP gateway."""

    @abstractmethod
    def get_langchain_tools(self, authorizer: ToolAuthorizer | None = None) -> list[Any]:
        """Return the LangChain tools an agent uses to discover and call gateway tools.

        When an ``authorizer`` is given, every ``gateway_call_tool`` invocation is checked by
        it first, and a refusal is surfaced to the agent as a tool error.
        """

    @abstractmethod
    async def get_domain_catalog(self, domain: str) -> list[Any]:
        """Return the full tool index (name, description, upstream, parameters) for one domain.

        Follows the gateway's pagination to return every tool. Used to inject the domain's tool
        list, with each tool's exact argument names, into a specialist prompt. Raises on gateway
        failure instead of returning an empty list, so a broken gateway is never mistaken for an
        empty domain.
        """

    @abstractmethod
    async def verify_connection(self) -> None:
        """Check that the gateway is reachable and accepts our credentials.

        Raises:
            MCPAuthenticationError: credentials were rejected.
            MCPEndpointNotFoundError: the configured URL is not an MCP endpoint.
            MCPGatewayUnreachableError: the gateway could not be reached.
            MCPGatewayError: any other unexpected gateway failure.
        """

    @abstractmethod
    def is_read_only(self, tool_name: str) -> bool | None:
        """Return the upstream's explicit read-only hint for a tool, or ``None`` when unknown.

        Implementations cache the hints carried by a domain catalog or a tool schema. ``None``
        (no catalog loaded yet, or the upstream declared nothing) tells the tool gate to fall back
        to its name-based policy. An explicit ``False`` means the upstream says the tool may change
        state and must not be overridden by a name pattern.
        """
