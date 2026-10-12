"""Unit tests for StreamableHttpUpstreamClient and Langfuse upstream integration."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from sector5_mcp.domain.models.upstream import UpstreamType
from sector5_mcp.infrastructure.upstream.http import StreamableHttpUpstreamClient


def test_streamable_http_upstream_client_init():
    client = StreamableHttpUpstreamClient(
        url="https://example.invalid/mcp",
        auth=("dummy-public-key", "dummy-secret-key"),
        upstream_type=UpstreamType.LANGFUSE,
        prefix="langfuse_",
    )

    assert client.url == "https://example.invalid/mcp"
    assert client.auth == ("dummy-public-key", "dummy-secret-key")
    assert client.upstream_type == UpstreamType.LANGFUSE
    assert client.prefix == "langfuse_"


@pytest.mark.asyncio
async def test_streamable_http_upstream_client_list_tools():
    client = StreamableHttpUpstreamClient(
        url="https://example.invalid/mcp",
        auth=("dummy-public-key", "dummy-secret-key"),
        upstream_type=UpstreamType.LANGFUSE,
        prefix="langfuse_",
    )

    tool1 = SimpleNamespace(
        name="queryMetrics",
        description="Query aggregated metrics",
        inputSchema={"type": "object", "properties": {"view": {"type": "string"}}},
        annotations={"readOnlyHint": True},
    )

    mock_session = AsyncMock()
    mock_session.list_tools.return_value = SimpleNamespace(tools=[tool1])

    with patch.object(client, "_ensure_connected", return_value=mock_session):
        tools = await client.list_tools()

    assert len(tools) == 1
    assert tools[0].name == "langfuse_queryMetrics"
    assert tools[0].description == "Query aggregated metrics"
    assert tools[0].parameters == {"type": "object", "properties": {"view": {"type": "string"}}}
    assert tools[0].upstream_type == UpstreamType.LANGFUSE
    assert tools[0].read_only is True


@pytest.mark.asyncio
async def test_streamable_http_upstream_client_call_tool():
    client = StreamableHttpUpstreamClient(
        url="https://example.invalid/mcp",
        auth=("dummy-public-key", "dummy-secret-key"),
        upstream_type=UpstreamType.LANGFUSE,
        prefix="langfuse_",
    )

    mock_session = AsyncMock()
    mock_content = SimpleNamespace(type="text", text='{"traceId": "t-1", "sum_totalCost": 0.005}')
    mock_session.call_tool.return_value = SimpleNamespace(content=[mock_content], isError=False)

    with patch.object(client, "_ensure_connected", return_value=mock_session):
        res = await client.call_tool("langfuse_queryMetrics", {"view": "observations"})

    assert res.status == "success"
    assert res.content == '{"traceId": "t-1", "sum_totalCost": 0.005}'
    mock_session.call_tool.assert_awaited_once_with(
        name="queryMetrics",
        arguments={"view": "observations"},
    )
