"""FastMCP client infrastructure adapter implementing MCPClientInterface."""

import contextlib
import logging
from typing import Any

import httpx
from fastmcp import Client
from lyoko.config import settings
from lyoko.domain.exceptions.mcp import (
    MCPAuthenticationError,
    MCPEndpointNotFoundError,
    MCPGatewayError,
    MCPGatewayUnreachableError,
)
from lyoko.domain.interfaces.mcp import MCPClientInterface, ToolAuthorizer
from lyoko.infrastructure.mcp.langchain_tools import build_gateway_tools

logger = logging.getLogger("lyoko.infrastructure.mcp.client")

_PROBE_TIMEOUT_SECONDS = 5.0
_PROBE_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
}
_INITIALIZE_REQUEST = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-03-26",
        "capabilities": {},
        "clientInfo": {"name": "lyoko-connectivity-check", "version": "0"},
    },
}
# Gateway meta-tools are called directly; every upstream tool is routed via gateway_call_tool.
_GATEWAY_META_TOOLS = (
    "gateway_get_domain_tools",
    "gateway_get_tool_schema",
    "gateway_call_tool",
)
# Domain catalog paging: the gateway caps a page at 100 entries; cap the full fetch too.
_DOMAIN_PAGE_SIZE = 100
_MAX_DOMAIN_PAGES = 20
# Hard ceiling for a single gateway tool call: a dropped Streamable HTTP session would otherwise
# leave the client waiting forever for a reply that never arrives and hang the agent request.
_CALL_TIMEOUT_SECONDS = 60.0


def _root_cause(exc: BaseException) -> BaseException:
    """Unwrap (nested) exception groups raised by anyio task groups to the first leaf error."""
    while isinstance(exc, BaseExceptionGroup) and exc.exceptions:
        exc = exc.exceptions[0]
    return exc


class FastMCPClient(MCPClientInterface):
    def __init__(self, server_url: str | None = None, token: str | None = None):
        self.server_url = server_url or settings.mcp_server_url
        raw_token = token if token is not None else settings.service_token
        self.token = (
            raw_token.get_secret_value()
            if hasattr(raw_token, "get_secret_value")
            else str(raw_token)
            if raw_token
            else ""
        )
        # Upstream-declared read-only hints, keyed by tool name. Populated lazily as the agent
        # discovers a domain catalog or reads a tool schema; the tool gate consults it so an
        # upstream's explicit readOnlyHint can override the name-based pattern heuristic.
        self._readonly_hints: dict[str, bool] = {}

    def _remember_readonly_hints(self, tools: list[dict[str, Any]]) -> None:
        """Cache the read-only hints of index entries that carry one."""
        for tool in tools:
            name = tool.get("name")
            hint = tool.get("read_only_hint")
            if name and isinstance(hint, bool):
                self._readonly_hints[name] = hint

    def is_read_only(self, tool_name: str) -> bool | None:
        """Return the upstream's read-only hint for ``tool_name``, or ``None`` when unknown.

        ``None`` (no catalog loaded yet, or the upstream declared nothing) leaves the decision to
        the gate's name-based policy. An explicit ``False`` marks a tool the upstream says may
        change state, and must not be overridden by a name pattern.
        """
        if tool_name in self._readonly_hints:
            return self._readonly_hints[tool_name]
        base_name = tool_name.split(".", 1)[1] if "." in tool_name else tool_name
        return self._readonly_hints.get(base_name)

    # ------------------------------------------------------------------
    # Diagnostics
    # ------------------------------------------------------------------

    def _error_for_status(self, status_code: int) -> MCPGatewayError | None:
        """Map the HTTP status returned by the gateway to a precise domain error."""
        if status_code < 400:
            return None
        if status_code in (401, 403):
            hint = (
                "Check that SERVICE_TOKEN matches the gateway's service token."
                if self.token
                else "No SERVICE_TOKEN is configured for LYOKO."
            )
            return MCPAuthenticationError(
                f"MCP gateway at {self.server_url} rejected the credentials "
                f"(HTTP {status_code}). {hint}"
            )
        if status_code == 404:
            return MCPEndpointNotFoundError(
                f"MCP gateway at {self.server_url} returned HTTP 404. MCP_SERVER_URL must "
                "point at the Streamable HTTP endpoint (usually ending in /mcp)."
            )
        return MCPGatewayError(f"MCP gateway at {self.server_url} returned HTTP {status_code}.")

    async def _probe(self) -> MCPGatewayError | None:
        """Send a raw MCP ``initialize`` request to learn the real HTTP status of the gateway.

        fastmcp collapses 401/403/500 into one generic error, so a plain HTTP request is the
        only reliable way to tell a wrong token from a wrong URL or a broken gateway.
        """
        headers = dict(_PROBE_HEADERS)
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        try:
            async with httpx.AsyncClient(
                timeout=_PROBE_TIMEOUT_SECONDS, follow_redirects=True
            ) as http:
                response = await http.post(
                    self.server_url, json=_INITIALIZE_REQUEST, headers=headers
                )
                session_id = response.headers.get("mcp-session-id")
                if response.status_code < 400 and session_id:
                    # Best-effort cleanup so the probe does not leave an idle session behind.
                    with contextlib.suppress(httpx.HTTPError):
                        await http.delete(
                            self.server_url, headers={**headers, "Mcp-Session-Id": session_id}
                        )
        except httpx.TransportError as exc:
            return MCPGatewayUnreachableError(
                f"MCP gateway at {self.server_url} is unreachable: {type(exc).__name__}: {exc}"
            )
        return self._error_for_status(response.status_code)

    async def _to_gateway_error(self, operation: str, exc: BaseException) -> MCPGatewayError:
        """Convert any failure into a domain error, enriched with the probed root cause."""
        if isinstance(exc, MCPGatewayError):
            return exc
        diagnosed = await self._probe()
        if diagnosed is not None:
            error = diagnosed
        else:
            cause = _root_cause(exc)
            error = MCPGatewayError(f"MCP gateway {operation} failed: {cause}")
        logger.error("MCP gateway %s failed: %s", operation, error)
        return error

    async def verify_connection(self) -> None:
        """Raise a typed domain error unless the gateway accepts our connection and credentials."""
        error = await self._probe()
        if error is not None:
            raise error
        logger.info("MCP gateway connectivity check passed (%s).", self.server_url)

    # ------------------------------------------------------------------
    # Gateway operations
    # ------------------------------------------------------------------

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        """Call a tool on sector5-mcp gateway, routing upstream tools through gateway_call_tool.

        Failures are returned as ``{"status": "failed", ...}`` so remediation workflows can
        branch on them without exception handling.
        """
        auth_token = self.token if self.token else None
        try:
            async with Client(self.server_url, auth=auth_token) as client:
                if name in _GATEWAY_META_TOOLS:
                    res = await client.call_tool(name, arguments, timeout=_CALL_TIMEOUT_SECONDS)
                else:
                    res = await client.call_tool(
                        "gateway_call_tool",
                        {"tool_name": name, "arguments": arguments},
                        timeout=_CALL_TIMEOUT_SECONDS,
                    )
                return res.data
        except Exception as exc:
            error = await self._to_gateway_error(f"tool call '{name}'", exc)
            return {"error": str(error), "status": "failed", "tool": name}

    async def get_domain_tools_page(
        self,
        domain: str,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Fetch one page of a domain's tool index from the gateway.

        Returns the gateway payload: ``domain``, ``total``, ``limit``, ``offset``, ``has_more``
        and ``tools`` (lean index entries: name, description, upstream).

        Raises:
            MCPGatewayError: if the gateway cannot be used or reports the domain has no tools,
                so a broken gateway is never mistaken for an empty domain.
        """
        auth_token = self.token if self.token else None
        try:
            async with Client(self.server_url, auth=auth_token) as client:
                res = await client.call_tool(
                    "gateway_get_domain_tools",
                    {"domain": domain, "limit": limit, "offset": offset},
                    timeout=_CALL_TIMEOUT_SECONDS,
                )
                data = res.data
        except Exception as exc:
            raise await self._to_gateway_error(f"list domain tools for '{domain}'", exc) from exc

        if isinstance(data, dict) and data.get("error"):
            raise MCPGatewayError(str(data["error"]))
        if not isinstance(data, dict):
            return {
                "domain": domain,
                "total": 0,
                "limit": limit,
                "offset": offset,
                "has_more": False,
                "tools": [],
            }
        raw_tools = data.get("tools") or []
        tools = [t for t in raw_tools if isinstance(t, dict) and t.get("name")]
        self._remember_readonly_hints(tools)
        return {**data, "tools": tools}

    async def get_domain_catalog(
        self,
        domain: str,
        page_size: int = _DOMAIN_PAGE_SIZE,
    ) -> list[dict[str, Any]]:
        """Fetch the full lean tool index for one upstream domain, following pagination.

        Used to inject a domain's tool list into a specialist prompt. Raises on gateway failure
        instead of returning an empty list, so a broken gateway is never mistaken for an empty
        domain.
        """
        tools: list[dict[str, Any]] = []
        offset = 0
        for _ in range(_MAX_DOMAIN_PAGES):
            page = await self.get_domain_tools_page(domain, limit=page_size, offset=offset)
            page_tools = page.get("tools") or []
            tools.extend(page_tools)
            if not page.get("has_more") or not page_tools:
                break
            offset = int(page.get("offset", offset)) + len(page_tools)
        return tools

    async def get_tool_schema(self, tool_name: str) -> Any:
        """Return the full parameter schema of a gateway tool.

        Raises:
            MCPGatewayError: if the gateway cannot be used.
        """
        auth_token = self.token if self.token else None
        try:
            async with Client(self.server_url, auth=auth_token) as client:
                res = await client.call_tool(
                    "gateway_get_tool_schema",
                    {"tool_name": tool_name},
                    timeout=_CALL_TIMEOUT_SECONDS,
                )
                data = res.data
        except Exception as exc:
            raise await self._to_gateway_error(f"get schema of '{tool_name}'", exc) from exc
        self._remember_schema_hint(tool_name, data)
        return data

    def _remember_schema_hint(self, tool_name: str, data: Any) -> None:
        """Cache the read-only hint carried by a tool schema response, when present."""
        if not isinstance(data, dict):
            return
        annotations = data.get("annotations") or {}
        hint = annotations.get("readOnlyHint", annotations.get("read_only_hint"))
        if isinstance(hint, bool):
            self._readonly_hints[data.get("name") or tool_name] = hint

    def get_langchain_tools(self, authorizer: ToolAuthorizer | None = None) -> list[Any]:
        """Return LangChain tools to discover and execute homelab tools across domains.

        The surface is deliberately three tools: discover a domain's index, read one tool's
        schema, execute it. Gateway failures are raised as ``ToolException`` so the LLM sees the
        real error instead of an empty result it could mistake for "nothing is available".

        When ``authorizer`` is given it is consulted before every ``gateway_call_tool``
        invocation; a refusal is raised as a ``ToolException`` and the tool never runs.
        """
        return build_gateway_tools(self, authorizer)
