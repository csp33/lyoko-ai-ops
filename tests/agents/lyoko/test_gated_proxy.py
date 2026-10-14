"""Tests for the gated run context and the gated MCP proxy."""

from typing import Any

import pytest
from fastmcp import Client
from lyoko.application.safety.gated_run_context import GatedRunContext
from lyoko.infrastructure.mcp.gated_proxy import BearerGuard, build_gated_mcp


@pytest.mark.asyncio
async def test_context_refuses_without_active_run():
    ctx = GatedRunContext()
    result = await ctx.call("gateway_call_tool", {"tool_name": "x"})
    assert result.startswith("Refused")


@pytest.mark.asyncio
async def test_context_routes_to_active_callback_then_closes():
    ctx = GatedRunContext()
    seen: list[tuple[str, dict[str, Any]]] = []

    async def cb(name: str, args: dict[str, Any]) -> str:
        seen.append((name, args))
        return "ok"

    async with ctx.activate(cb):
        assert ctx.is_active
        assert await ctx.call("a", {"k": 1}) == "ok"
    assert not ctx.is_active
    assert seen == [("a", {"k": 1})]
    assert (await ctx.call("a", {})).startswith("Refused")


@pytest.mark.asyncio
async def test_proxy_tools_route_through_context():
    ctx = GatedRunContext()
    seen: list[tuple[str, dict[str, Any]]] = []

    async def cb(name: str, args: dict[str, Any]) -> str:
        seen.append((name, args))
        return "done"

    async with ctx.activate(cb), Client(build_gated_mcp(ctx)) as client:
        await client.call_tool(
            "gateway_call_tool", {"tool_name": "k8s_get_pods", "arguments": {"namespace": "a"}}
        )
    assert seen == [
        ("gateway_call_tool", {"tool_name": "k8s_get_pods", "arguments": {"namespace": "a"}})
    ]


@pytest.mark.asyncio
async def test_bearer_guard_rejects_and_accepts():
    reached: list[bool] = []

    async def inner(scope, receive, send):
        reached.append(True)

    guard = BearerGuard(inner, "secret")
    sent: list[dict[str, Any]] = []

    async def send(msg):
        sent.append(msg)

    for headers in ([], [(b"authorization", b"Bearer wrong")]):
        await guard({"type": "http", "headers": headers}, None, send)
    assert not reached
    assert [m["status"] for m in sent if m["type"] == "http.response.start"] == [401, 401]

    await guard({"type": "http", "headers": [(b"authorization", b"Bearer secret")]}, None, send)
    assert reached
