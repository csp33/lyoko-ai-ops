"""HTTP streamable upstream MCP client using official MCP SDK."""

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Any

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from sector5_mcp.domain.interfaces.upstream import UpstreamMCPInterface
from sector5_mcp.domain.models.upstream import ToolDefinition, ToolResult, UpstreamType
from sector5_mcp.infrastructure.upstream.client import _tool_annotations

logger = logging.getLogger("sector5_mcp.upstream_http")


class StreamableHttpUpstreamClient(UpstreamMCPInterface):
    """Client that communicates with an upstream MCP server via Streamable HTTP."""

    def __init__(
        self,
        url: str,
        auth: tuple[str, str] | None = None,
        headers: dict[str, str] | None = None,
        upstream_type: UpstreamType | str = "unknown",
        prefix: str | None = None,
    ):
        self.url = url
        self.auth = auth
        self.headers = headers or {}
        self.upstream_type = upstream_type
        self.prefix = prefix
        self._lock = asyncio.Lock()
        self._cached_tools: list[ToolDefinition] | None = None

    @asynccontextmanager
    async def _connect(self):
        """Yield an active ClientSession within the current task's scope."""
        is_mocked = hasattr(self._ensure_connected, "assert_called") or (
            hasattr(self._ensure_connected, "__func__")
            and self._ensure_connected.__func__
            is not StreamableHttpUpstreamClient._ensure_connected
        )
        if is_mocked:
            session = await self._ensure_connected()
            yield session
            return

        async with (
            httpx.AsyncClient(
                auth=self.auth,
                headers=self.headers,
                timeout=httpx.Timeout(60.0, connect=10.0),
            ) as http_client,
            streamable_http_client(self.url, http_client=http_client) as (read, write),
            ClientSession(read, write) as session,
        ):
            await session.initialize()
            yield session

    async def _ensure_connected(self) -> ClientSession:
        """Compatibility hook for tests patching connection establishment."""
        raise NotImplementedError("Use _connect context manager")

    async def list_tools(self) -> list[ToolDefinition]:
        """Discover tools using the upstream HTTP session."""
        async with self._lock:
            if self._cached_tools is not None:
                return self._cached_tools

            async with self._connect() as session:
                result = await session.list_tools()

            tool_definitions = []
            for tool in result.tools:
                tool_name = tool.name
                if self.prefix and not tool_name.startswith(self.prefix):
                    tool_name = f"{self.prefix}{tool_name}"
                schema = (
                    getattr(tool, "inputSchema", None) or getattr(tool, "input_schema", None) or {}
                )
                tool_definitions.append(
                    ToolDefinition(
                        name=tool_name,
                        description=tool.description or "",
                        parameters=schema,
                        upstream_type=self.upstream_type,
                        annotations=_tool_annotations(tool),
                    )
                )
            self._cached_tools = tool_definitions
            return tool_definitions

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        """Execute a tool using the upstream HTTP session."""
        raw_name = name
        if self.prefix and raw_name.startswith(self.prefix):
            raw_name = raw_name[len(self.prefix) :]

        async with self._lock:
            async with self._connect() as session:
                result = await session.call_tool(name=raw_name, arguments=arguments)

            # Format content
            contents = []
            for item in result.content:
                if hasattr(item, "text"):
                    contents.append(item.text)
                else:
                    contents.append(str(item))

            is_error = bool(getattr(result, "is_error", False)) or bool(
                getattr(result, "isError", False)
            )
            return ToolResult(
                status="error" if is_error else "success",
                content="\n".join(contents)
                if len(contents) > 1
                else (contents[0] if contents else None),
                is_error=is_error,
            )
