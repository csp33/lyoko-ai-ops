"""Gated MCP proxy: the only tool surface an external Hermes gateway is given.

Hermes connects here instead of to sector5-mcp. Every call is forwarded to the active run's
gated callback, so the operator approval flow (Telegram buttons) cannot be bypassed: Hermes holds
no sector5-mcp credentials and its native tools are disabled in its config.
"""

import hmac
import logging
from typing import Any

from fastmcp import FastMCP
from lyoko.application.safety.gated_run_context import GatedRunContext

logger = logging.getLogger("lyoko.infrastructure.mcp.gated_proxy")

GATED_MCP_MOUNT_PATH = "/gated-mcp"
_GATED_MCP_ENDPOINT = "/mcp"


def build_gated_mcp(context: GatedRunContext) -> FastMCP:
    """Expose the gateway discovery trio, each call routed through ``context``."""
    mcp = FastMCP("lyoko-gated-gateway")

    @mcp.tool
    async def gateway_get_domain_tools(domain: str, limit: int = 50, offset: int = 0) -> str:
        """List one page of the tools of one upstream domain (kubernetes, unifi, homeassistant, grafana, github, telegram)."""
        return await context.call(
            "gateway_get_domain_tools", {"domain": domain, "limit": limit, "offset": offset}
        )

    @mcp.tool
    async def gateway_get_tool_schema(tool_name: str) -> str:
        """Get the full parameter schema and description of one gateway tool."""
        return await context.call("gateway_get_tool_schema", {"tool_name": tool_name})

    @mcp.tool
    async def gateway_call_tool(tool_name: str, arguments: dict[str, Any] | None = None) -> str:
        """Execute a gateway tool by exact name. State-changing tools require operator approval."""
        return await context.call(
            "gateway_call_tool", {"tool_name": tool_name, "arguments": arguments or {}}
        )

    return mcp


class BearerGuard:
    """ASGI middleware rejecting HTTP requests that lack the expected bearer token."""

    def __init__(self, app: Any, token: str) -> None:
        self._app = app
        self._expected = f"Bearer {token}".encode()

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] == "http":
            headers = dict(scope.get("headers") or [])
            provided = headers.get(b"authorization", b"")
            if not hmac.compare_digest(provided, self._expected):
                logger.warning("Rejected gated MCP request with a missing or wrong token.")
                await send(
                    {
                        "type": "http.response.start",
                        "status": 401,
                        "headers": [(b"content-type", b"application/json")],
                    }
                )
                await send({"type": "http.response.body", "body": b'{"error":"unauthorized"}'})
                return
        await self._app(scope, receive, send)


def build_gated_mcp_app(context: GatedRunContext, token: str) -> tuple[Any, Any]:
    """Return ``(guarded_asgi_app, inner_app)``; the inner app owns the lifespan to run."""
    inner = build_gated_mcp(context).http_app(path=_GATED_MCP_ENDPOINT)
    return BearerGuard(inner, token), inner
