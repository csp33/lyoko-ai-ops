"""Tests for FastMCPClient failure diagnosis and the startup gateway check."""

import http.server
import socketserver
import threading
from collections.abc import Iterator
from unittest.mock import AsyncMock, patch

import pytest
from langchain_core.callbacks import BaseCallbackHandler
from lyoko.composition import verify_mcp_gateway
from lyoko.domain.exceptions.mcp import (
    MCPAuthenticationError,
    MCPEndpointNotFoundError,
    MCPGatewayError,
    MCPGatewayUnreachableError,
)
from lyoko.infrastructure.mcp.client import FastMCPClient

SECRET_TOKEN = "super-secret-service-token"


class _StatusServer:
    """Tiny HTTP server answering every request with a fixed status code."""

    def __init__(self, status_code: int) -> None:
        status = status_code

        class Handler(http.server.BaseHTTPRequestHandler):
            def _respond(self) -> None:
                self.send_response(status)
                self.send_header("Content-Length", "0")
                self.end_headers()

            do_POST = _respond
            do_DELETE = _respond
            do_GET = _respond

            def log_message(self, *args: object) -> None:
                pass

        self._server = socketserver.TCPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self._server.server_address[1]}/mcp"
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()


@pytest.fixture
def status_server() -> Iterator[type[_StatusServer]]:
    servers: list[_StatusServer] = []

    def factory(status_code: int) -> _StatusServer:
        server = _StatusServer(status_code)
        servers.append(server)
        return server

    yield factory  # type: ignore[misc]
    for server in servers:
        server.close()


@pytest.mark.asyncio
async def test_verify_connection_passes_on_success(status_server):
    server = status_server(200)
    await FastMCPClient(server_url=server.url, token=SECRET_TOKEN).verify_connection()


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [401, 403])
async def test_verify_connection_reports_missing_token(status_server, status_code):
    server = status_server(status_code)
    with pytest.raises(MCPAuthenticationError, match="No SERVICE_TOKEN is configured"):
        await FastMCPClient(server_url=server.url, token="").verify_connection()


@pytest.mark.asyncio
async def test_verify_connection_reports_wrong_token_without_leaking_it(status_server):
    server = status_server(401)
    with pytest.raises(MCPAuthenticationError) as exc_info:
        await FastMCPClient(server_url=server.url, token=SECRET_TOKEN).verify_connection()
    assert "matches the gateway's service token" in str(exc_info.value)
    assert SECRET_TOKEN not in str(exc_info.value)


@pytest.mark.asyncio
async def test_verify_connection_reports_wrong_path(status_server):
    server = status_server(404)
    with pytest.raises(MCPEndpointNotFoundError, match="/mcp"):
        await FastMCPClient(server_url=server.url, token=SECRET_TOKEN).verify_connection()


@pytest.mark.asyncio
async def test_verify_connection_reports_unreachable_gateway():
    client = FastMCPClient(server_url="http://127.0.0.1:1/mcp", token=SECRET_TOKEN)
    with pytest.raises(MCPGatewayUnreachableError):
        await client.verify_connection()


@pytest.mark.asyncio
async def test_verify_connection_reports_other_http_errors(status_server):
    server = status_server(500)
    with pytest.raises(MCPGatewayError, match="HTTP 500") as exc_info:
        await FastMCPClient(server_url=server.url, token=SECRET_TOKEN).verify_connection()
    assert not exc_info.value.is_configuration_error


@pytest.mark.asyncio
async def test_domain_catalog_raises_instead_of_returning_empty_list(status_server):
    """Regression: a 401 used to be logged as a warning and swallowed as `[]`."""
    server = status_server(401)
    client = FastMCPClient(server_url=server.url, token="")
    with pytest.raises(MCPAuthenticationError):
        await client.get_domain_catalog("kubernetes")


@pytest.mark.asyncio
async def test_call_tool_returns_actionable_failure(status_server):
    server = status_server(404)
    client = FastMCPClient(server_url=server.url, token=SECRET_TOKEN)
    result = await client.call_tool("k8s_get_pods", {"namespace": "default"})
    assert result["status"] == "failed"
    assert result["tool"] == "k8s_get_pods"
    assert "MCP_SERVER_URL" in result["error"]
    assert SECRET_TOKEN not in result["error"]


@pytest.mark.asyncio
async def test_langchain_tools_surface_gateway_error_to_the_llm(status_server):
    """The LLM must see the real cause so it can tell the user instead of guessing."""
    server = status_server(401)
    client = FastMCPClient(server_url=server.url, token="")
    tools = {t.name: t for t in client.get_langchain_tools()}

    for name, args in [
        ("gateway_get_domain_tools", {"domain": "kubernetes"}),
        ("gateway_get_tool_schema", {"tool_name": "k8s_get_pods"}),
        ("gateway_call_tool", {"tool_name": "k8s_get_pods", "arguments": {}}),
    ]:
        output = await tools[name].ainvoke(args)
        assert "rejected the credentials" in output, name


@pytest.mark.asyncio
async def test_startup_check_aborts_on_configuration_error():
    client = AsyncMock()
    client.verify_connection.side_effect = MCPAuthenticationError("bad token")
    with (
        patch("lyoko.composition.settings.mcp_fail_fast", True),
        pytest.raises(MCPAuthenticationError),
    ):
        await verify_mcp_gateway(client)


@pytest.mark.asyncio
async def test_startup_check_tolerates_configuration_error_when_fail_fast_disabled():
    client = AsyncMock()
    client.verify_connection.side_effect = MCPEndpointNotFoundError("wrong path")
    with patch("lyoko.composition.settings.mcp_fail_fast", False):
        await verify_mcp_gateway(client)


@pytest.mark.asyncio
async def test_startup_check_tolerates_unreachable_gateway():
    client = AsyncMock()
    client.verify_connection.side_effect = MCPGatewayUnreachableError("still starting")
    with patch("lyoko.composition.settings.mcp_fail_fast", True):
        await verify_mcp_gateway(client)


@pytest.mark.asyncio
async def test_langchain_tools_expose_domain_discovery_and_execution():
    tools = {t.name: t for t in FastMCPClient(server_url="http://x/mcp").get_langchain_tools()}

    assert set(tools) == {
        "gateway_get_domain_tools",
        "gateway_get_tool_schema",
        "gateway_call_tool",
    }
    assert "domain" in tools["gateway_get_domain_tools"].args


@pytest.mark.asyncio
async def test_authorizer_refusal_blocks_the_call_and_reaches_the_llm():
    client = FastMCPClient(server_url="http://x/mcp", token="")
    client.call_tool = AsyncMock(return_value={"ok": True})
    authorizer = AsyncMock(return_value="Refused: not allowed")
    tools = {t.name: t for t in client.get_langchain_tools(authorizer=authorizer)}

    output = await tools["gateway_call_tool"].ainvoke(
        {"tool_name": "pods_delete", "arguments": {"name": "x"}}
    )

    assert output == "Refused: not allowed"
    authorizer.assert_awaited_once_with("pods_delete", {"name": "x"})
    client.call_tool.assert_not_called()


@pytest.mark.asyncio
async def test_authorizer_allowing_the_call_lets_it_through():
    client = FastMCPClient(server_url="http://x/mcp", token="")
    client.call_tool = AsyncMock(return_value={"ok": True})
    authorizer = AsyncMock(return_value=None)
    tools = {t.name: t for t in client.get_langchain_tools(authorizer=authorizer)}

    output = await tools["gateway_call_tool"].ainvoke(
        {"tool_name": "pods_get", "arguments": {"name": "x"}}
    )

    assert output == "{'ok': True}"
    client.call_tool.assert_awaited_once_with("pods_get", {"name": "x"})


@pytest.mark.asyncio
async def test_call_tool_merges_arguments_flattened_to_the_top_level():
    """Models often flatten the target args; the wrapper must still execute the call."""
    client = FastMCPClient(server_url="http://x/mcp", token="")
    client.call_tool = AsyncMock(return_value={"ok": True})
    tools = {t.name: t for t in client.get_langchain_tools()}

    await tools["gateway_call_tool"].ainvoke(
        {"tool_name": "unifi_list_wlans", "enabled_only": True, "limit": 25}
    )

    client.call_tool.assert_awaited_once_with(
        "unifi_list_wlans", {"enabled_only": True, "limit": 25}
    )


@pytest.mark.asyncio
async def test_domain_discovery_tool_is_never_gated():
    client = FastMCPClient(server_url="http://x/mcp", token="")
    client.get_domain_tools_page = AsyncMock(
        return_value={
            "domain": "kubernetes",
            "total": 1,
            "limit": 50,
            "offset": 0,
            "has_more": False,
            "tools": [{"name": "pods_list", "description": "List pods"}],
        }
    )
    authorizer = AsyncMock(return_value="Refused")
    tools = {t.name: t for t in client.get_langchain_tools(authorizer=authorizer)}

    output = await tools["gateway_get_domain_tools"].ainvoke({"domain": "kubernetes"})

    assert "pods_list" in output
    authorizer.assert_not_called()


@pytest.mark.asyncio
async def test_domain_tools_are_scoped_execution_and_schema():
    """Specialists receive call and schema tools; discovery is in the prompt so it is omitted."""
    client = FastMCPClient(server_url="http://x/mcp", token="")

    tools = {t.name: t for t in await client.get_domain_langchain_tools("grafana")}

    assert set(tools) == {
        "gateway_get_tool_schema",
        "gateway_call_tool",
    }


@pytest.mark.asyncio
async def test_get_domain_catalog_follows_pagination():
    client = FastMCPClient(server_url="http://x/mcp", token="")
    client.get_domain_tools_page = AsyncMock(
        side_effect=[
            {
                "domain": "unifi",
                "total": 3,
                "limit": 2,
                "offset": 0,
                "has_more": True,
                "tools": [{"name": "a"}, {"name": "b"}],
            },
            {
                "domain": "unifi",
                "total": 3,
                "limit": 2,
                "offset": 2,
                "has_more": False,
                "tools": [{"name": "c"}],
            },
        ]
    )

    catalog = await client.get_domain_catalog("unifi")

    assert [t["name"] for t in catalog] == ["a", "b", "c"]
    assert client.get_domain_tools_page.await_count == 2


class _RunRecorder(BaseCallbackHandler):
    """Records every chain run (name, id, parent) the way a tracing backend would see it."""

    def __init__(self) -> None:
        self.runs: list[tuple[str, object, object]] = []
        self.tool_runs: list[tuple[str, object]] = []

    def on_chain_start(self, serialized, inputs, *, run_id, parent_run_id=None, name=None, **kw):
        self.runs.append((name or "", run_id, parent_run_id))

    def on_tool_start(self, serialized, input_str, *, run_id, **kwargs):
        self.tool_runs.append((serialized.get("name", ""), run_id))


@pytest.mark.asyncio
async def test_gateway_call_is_traced_under_the_name_of_the_real_tool():
    """Traces must say `mcp:pods_log`, not just `gateway_call_tool`, to be readable."""
    client = FastMCPClient(server_url="http://x/mcp", token="")
    client.call_tool = AsyncMock(return_value={"ok": True})
    tools = {t.name: t for t in client.get_langchain_tools()}
    recorder = _RunRecorder()

    await tools["gateway_call_tool"].ainvoke(
        {"tool_name": "pods_log", "arguments": {"name": "radarr"}},
        config={"callbacks": [recorder]},
    )

    ((tool_name, tool_run_id),) = recorder.tool_runs
    assert tool_name == "gateway_call_tool"
    ((span_name, _, parent),) = [r for r in recorder.runs if r[0].startswith("mcp:")]
    assert span_name == "mcp:pods_log"
    assert parent == tool_run_id


@pytest.mark.asyncio
async def test_refused_gateway_call_is_still_traced_under_the_tool_name():
    client = FastMCPClient(server_url="http://x/mcp", token="")
    client.call_tool = AsyncMock()
    authorizer = AsyncMock(return_value="Refused: read-only phase")
    tools = {t.name: t for t in client.get_langchain_tools(authorizer=authorizer)}
    recorder = _RunRecorder()

    output = await tools["gateway_call_tool"].ainvoke(
        {"tool_name": "pods_delete", "arguments": {"name": "x"}},
        config={"callbacks": [recorder]},
    )

    assert output == "Refused: read-only phase"
    assert "mcp:pods_delete" in [r[0] for r in recorder.runs]
    client.call_tool.assert_not_called()


@pytest.mark.asyncio
async def test_empty_tool_result_is_formatted_clearly():
    """Empty content returned from gateway tools should be formatted with clear descriptive text."""
    client = FastMCPClient(server_url="http://x/mcp", token="")
    client.call_tool = AsyncMock(
        return_value={"status": "success", "content": "", "is_error": False}
    )
    tools = {t.name: t for t in client.get_langchain_tools()}

    output = await tools["gateway_call_tool"].ainvoke(
        {"tool_name": "k8s_pods_list", "arguments": {"namespace": "default"}}
    )
    assert "No resources found or empty result." in output


class _ResultForTimeout:
    def __init__(self, data: object) -> None:
        self.data = data


class _RecordingClient:
    """Stand-in for fastmcp.Client that records the timeout it was given."""

    seen: dict[str, object] = {}

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        pass

    async def __aenter__(self) -> "_RecordingClient":
        return self

    async def __aexit__(self, *_exc: object) -> bool:
        return False

    async def call_tool(self, name: str, arguments: dict, *, timeout: object = None) -> object:
        _RecordingClient.seen = {"name": name, "timeout": timeout}
        return _ResultForTimeout({"status": "success", "content": "ok"})


@pytest.mark.asyncio
async def test_call_tool_bounds_the_gateway_call_with_a_timeout():
    """Regression: a dropped session must not let a tool call wait forever."""
    client = FastMCPClient(server_url="http://x/mcp", token="")
    with (
        patch("lyoko.infrastructure.mcp.client.Client", _RecordingClient),
        patch("lyoko.infrastructure.mcp.client._CALL_TIMEOUT_SECONDS", 12.0),
    ):
        await client.call_tool("k8s_pods_list", {})

    assert _RecordingClient.seen["name"] == "gateway_call_tool"
    assert _RecordingClient.seen["timeout"] == 12.0


def test_is_read_only_is_unknown_before_any_catalog_is_loaded():
    client = FastMCPClient(server_url="http://x/mcp", token="")
    assert client.is_read_only("grafana_query_prometheus") is None


def test_remembered_readonly_hints_are_tri_state_and_namespace_aware():
    client = FastMCPClient(server_url="http://x/mcp", token="")
    client._remember_readonly_hints(
        [
            {"name": "grafana_query_prometheus", "read_only_hint": True},
            {"name": "resources_scale", "read_only_hint": False},
            {"name": "pods_list"},
        ]
    )

    assert client.is_read_only("grafana_query_prometheus") is True
    assert client.is_read_only("resources_scale") is False
    assert client.is_read_only("pods_list") is None
    assert client.is_read_only("grafana.grafana_query_prometheus") is True


class _PayloadClient:
    """Stand-in for fastmcp.Client returning a fixed payload for any call."""

    payload: dict = {}

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        pass

    async def __aenter__(self) -> "_PayloadClient":
        return self

    async def __aexit__(self, *_exc: object) -> bool:
        return False

    async def call_tool(self, name: str, arguments: dict, *, timeout: object = None) -> object:
        return _ResultForTimeout(_PayloadClient.payload)


@pytest.mark.asyncio
async def test_domain_catalog_remembers_readonly_hints():
    client = FastMCPClient(server_url="http://x/mcp", token="")
    _PayloadClient.payload = {
        "domain": "grafana",
        "total": 2,
        "limit": 50,
        "offset": 0,
        "has_more": False,
        "tools": [
            {"name": "grafana_query_prometheus", "read_only_hint": True},
            {"name": "grafana_update_dashboard", "read_only_hint": False},
        ],
    }
    with patch("lyoko.infrastructure.mcp.client.Client", _PayloadClient):
        await client.get_domain_tools_page("grafana")

    assert client.is_read_only("grafana_query_prometheus") is True
    assert client.is_read_only("grafana_update_dashboard") is False


@pytest.mark.asyncio
async def test_tool_schema_remembers_the_readonly_hint():
    client = FastMCPClient(server_url="http://x/mcp", token="")
    _PayloadClient.payload = {
        "name": "grafana_query_prometheus",
        "description": "Query Prometheus",
        "annotations": {"readOnlyHint": True},
    }
    with patch("lyoko.infrastructure.mcp.client.Client", _PayloadClient):
        await client.get_tool_schema("grafana_query_prometheus")

    assert client.is_read_only("grafana_query_prometheus") is True


@pytest.mark.asyncio
async def test_call_tool_resolves_alias_key_for_tool_name():
    """Models often pass the tool name under 'tool', 'name', or 'target_tool'."""
    client = FastMCPClient(server_url="http://x/mcp", token="")
    client.call_tool = AsyncMock(return_value={"ok": True})
    tools = {t.name: t for t in client.get_langchain_tools()}

    await tools["gateway_call_tool"].ainvoke({"tool": "ha_get_logs", "source": "core", "limit": 10})
    client.call_tool.assert_awaited_once_with("ha_get_logs", {"source": "core", "limit": 10})


@pytest.mark.asyncio
async def test_call_tool_resolves_single_nested_key_matching_tool_name():
    """Models sometimes wrap the parameters under {tool_name: {args}}."""
    client = FastMCPClient(server_url="http://x/mcp", token="")
    client.call_tool = AsyncMock(return_value={"ok": True})
    tools = {t.name: t for t in client.get_langchain_tools()}

    await tools["gateway_call_tool"].ainvoke({"ha_get_logs": {"source": "core", "limit": 10}})
    client.call_tool.assert_awaited_once_with("ha_get_logs", {"source": "core", "limit": 10})


@pytest.mark.asyncio
async def test_call_tool_infers_tool_name_from_domain_catalog():
    """When tool_name is omitted, infer it if passed arguments uniquely match a domain tool."""
    client = FastMCPClient(server_url="http://x/mcp", token="")
    client.call_tool = AsyncMock(return_value={"ok": True})
    client.get_domain_catalog = AsyncMock(
        return_value=[
            {
                "name": "ha_get_logs",
                "parameters": {
                    "properties": {
                        "source": {"type": "string"},
                        "level": {"type": "string"},
                        "structured": {"type": "boolean"},
                    }
                },
            },
            {
                "name": "ha_get_state",
                "parameters": {
                    "properties": {
                        "entity_id": {"type": "string"},
                    }
                },
            },
        ]
    )
    tools = {t.name: t for t in await client.get_domain_langchain_tools("homeassistant")}

    output = await tools["gateway_call_tool"].ainvoke(
        {"source": "error_log", "level": "error", "structured": True}
    )
    assert output == "{'ok': True}"
    client.call_tool.assert_awaited_once_with(
        "ha_get_logs", {"source": "error_log", "level": "error", "structured": True}
    )


@pytest.mark.asyncio
async def test_call_tool_returns_actionable_error_when_tool_name_cannot_be_inferred():
    """When tool_name is completely missing and cannot be inferred, return actionable feedback."""
    client = FastMCPClient(server_url="http://x/mcp", token="")
    client.call_tool = AsyncMock()
    tools = {t.name: t for t in client.get_langchain_tools()}

    output = await tools["gateway_call_tool"].ainvoke({"foo": "bar", "baz": 123})
    assert "Missing required 'tool_name'" in output
    assert "gateway_call_tool" in output
