"""Process-based upstream MCP client using official MCP SDK with persistent sessions."""

import asyncio
import logging
import os
import shlex
from contextlib import AsyncExitStack
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from sector5_mcp.domain.interfaces.upstream import UpstreamMCPInterface
from sector5_mcp.domain.models.upstream import ToolDefinition, ToolResult, UpstreamType

logger = logging.getLogger("sector5_mcp.upstream_client")


def _tool_annotations(tool: Any) -> dict[str, Any]:
    """Normalize an MCP tool's optional annotations into a plain dict.

    The MCP SDK models annotations (e.g. ``readOnlyHint``) as a Pydantic object; older or
    third-party servers may send a plain dict. Either way the upstream's hint is preserved so it
    can be propagated to the gateway catalog and the agent's tool gate.
    """
    annotations = getattr(tool, "annotations", None)
    if annotations is None:
        return {}
    if isinstance(annotations, dict):
        return annotations
    if hasattr(annotations, "model_dump"):
        return annotations.model_dump(exclude_none=True, by_alias=True)
    return {}


class ProcessUpstreamClient(UpstreamMCPInterface):
    """Client that communicates with an upstream MCP server via a persistent stdio sub-process."""

    def __init__(
        self,
        command: str,
        args: list[str] | None = None,
        env: dict[str, str] | None = None,
        upstream_type: UpstreamType | str = "unknown",
        prefix: str | None = None,
    ):
        self.command = command
        self.args = args or []
        self.env = {**os.environ, **(env or {})}
        self.upstream_type = upstream_type
        self.prefix = prefix
        self._lock = asyncio.Lock()
        self._session: ClientSession | None = None
        self._exit_stack: AsyncExitStack | None = None

    def _get_server_params(self) -> StdioServerParameters:
        parts = shlex.split(self.command) if self.command else []
        cmd = parts[0] if parts else self.command
        args = parts[1:] + self.args
        return StdioServerParameters(
            command=cmd,
            args=args,
            env=self.env,
        )

    async def _ensure_connected(self) -> ClientSession:
        """Establish and initialize the persistent upstream process session if not already connected."""
        if self._session is not None:
            return self._session

        logger.info(
            f"Starting persistent upstream MCP process for '{self.upstream_type}' ({self.command})..."
        )
        server_params = self._get_server_params()
        self._exit_stack = AsyncExitStack()
        try:
            read, write = await self._exit_stack.enter_async_context(stdio_client(server_params))
            session = await self._exit_stack.enter_async_context(ClientSession(read, write))
            await session.initialize()
            self._session = session
            logger.info(f"Persistent upstream MCP session established for '{self.upstream_type}'.")
            return self._session
        except Exception as exc:
            logger.error(f"Failed to start upstream MCP process '{self.upstream_type}': {exc}")
            await self._close_session()
            raise

    async def _close_session(self) -> None:
        """Gracefully terminate upstream session and process."""
        if self._exit_stack:
            try:
                await self._exit_stack.aclose()
            except Exception as exc:
                logger.warning(
                    f"Error terminating upstream MCP process for '{self.upstream_type}': {exc}"
                )
            finally:
                self._session = None
                self._exit_stack = None

    async def list_tools(self) -> list[ToolDefinition]:
        """Discover tools using the persistent upstream session."""
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
                tool_definitions.append(
                    ToolDefinition(
                        name=tool_name,
                        description=tool.description or "",
                        parameters=getattr(tool, "input_schema", {}),
                        upstream_type=self.upstream_type,
                        annotations=_tool_annotations(tool),
                    )
                )
            return tool_definitions

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        """Execute a tool using the persistent upstream session with automatic recovery."""
        raw_name = name
        if self.prefix and raw_name.startswith(self.prefix):
            raw_name = raw_name[len(self.prefix) :]

        async with self._lock:
            try:
                session = await self._ensure_connected()
                result = await session.call_tool(raw_name, arguments)
            except Exception as exc:
                logger.warning(
                    f"Tool call '{raw_name}' failed on upstream '{self.upstream_type}', retrying with fresh connection: {exc}"
                )
                await self._close_session()
                session = await self._ensure_connected()
                result = await session.call_tool(raw_name, arguments)

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
