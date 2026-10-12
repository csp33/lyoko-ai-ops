"""HTTP streamable upstream MCP client using official MCP SDK with persistent sessions."""

import asyncio
import logging
from contextlib import AsyncExitStack
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
        self._session: ClientSession | None = None
        self._exit_stack: AsyncExitStack | None = None

    async def _ensure_connected(self) -> ClientSession:
        """Establish and initialize the persistent upstream HTTP session if not already connected."""
        if self._session is not None:
            return self._session

        logger.info(
            f"Starting persistent upstream MCP HTTP session for '{self.upstream_type}' ({self.url})..."
        )
        self._exit_stack = AsyncExitStack()
        try:
            http_client = httpx.AsyncClient(
                auth=self.auth,
                headers=self.headers,
                timeout=httpx.Timeout(60.0, connect=10.0),
            )
            await self._exit_stack.enter_async_context(http_client)
            read, write = await self._exit_stack.enter_async_context(
                streamable_http_client(self.url, http_client=http_client)
            )
            session = await self._exit_stack.enter_async_context(ClientSession(read, write))
            await session.initialize()
            self._session = session
            logger.info(
                f"Persistent upstream MCP HTTP session established for '{self.upstream_type}'."
            )
            return self._session
        except Exception as exc:
            logger.error(f"Failed to start upstream MCP HTTP session '{self.upstream_type}': {exc}")
            await self._close_session()
            raise

    async def _close_session(self) -> None:
        """Gracefully terminate upstream session and HTTP client."""
        if self._exit_stack:
            try:
                await self._exit_stack.aclose()
            except Exception as exc:
                logger.warning(
                    f"Error terminating upstream MCP HTTP session for '{self.upstream_type}': {exc}"
                )
            finally:
                self._session = None
                self._exit_stack = None

    async def list_tools(self) -> list[ToolDefinition]:
        """Discover tools using the persistent upstream HTTP session."""
        async with self._lock:
            try:
                session = await self._ensure_connected()
                result = await session.list_tools()
            except Exception as exc:
                logger.warning(
                    f"list_tools failed on upstream '{self.upstream_type}', attempting reconnection: {exc}"
                )
                await self._close_session()
                session = await self._ensure_connected()
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
            return tool_definitions

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        """Execute a tool using the persistent upstream HTTP session with automatic recovery."""
        raw_name = name
        if self.prefix and raw_name.startswith(self.prefix):
            raw_name = raw_name[len(self.prefix) :]

        async with self._lock:
            try:
                session = await self._ensure_connected()
                result = await session.call_tool(name=raw_name, arguments=arguments)
            except Exception as exc:
                logger.warning(
                    f"Tool call '{raw_name}' failed on upstream '{self.upstream_type}', retrying with fresh connection: {exc}"
                )
                await self._close_session()
                session = await self._ensure_connected()
                result = await session.call_tool(name=raw_name, arguments=arguments)

            # Format content
            contents = []
            for item in result.content:
                if hasattr(item, "text"):
                    contents.append(item.text)
                else:
                    contents.append(str(item))

            is_error = bool(getattr(result, "is_error", False))
            return ToolResult(
                status="error" if is_error else "success",
                content="\n".join(contents)
                if len(contents) > 1
                else (contents[0] if contents else None),
                is_error=is_error,
            )
