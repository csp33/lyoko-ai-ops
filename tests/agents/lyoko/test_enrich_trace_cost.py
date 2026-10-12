"""Unit tests for EnrichTraceCostUseCase."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from lyoko.application.incidents.use_cases.enrich_trace_cost import EnrichTraceCostUseCase
from lyoko.domain.interfaces.mcp import MCPClientInterface
from lyoko.domain.interfaces.tracer import TracerInterface


def test_enrich_trace_cost_disabled():
    use_case = EnrichTraceCostUseCase(enabled=False)
    res = pytest.importorskip("asyncio").run(use_case.execute("Hello world", "t-123"))
    assert res == "Hello world"


def test_enrich_trace_cost_no_trace_id():
    use_case = EnrichTraceCostUseCase(enabled=True)
    res = pytest.importorskip("asyncio").run(use_case.execute("Hello world", None))
    assert res == "Hello world"


@pytest.mark.asyncio
async def test_enrich_trace_cost_with_cost_and_url():
    mock_mcp = AsyncMock(spec=MCPClientInterface)
    mock_mcp.call_tool.return_value = MagicMock(
        content='[{"traceId": "t-123", "sum_totalCost": 0.02364}]',
        is_error=False,
    )

    mock_tracer = MagicMock(spec=TracerInterface)
    mock_tracer.get_trace_url.return_value = "https://cloud.langfuse.com/project/p1/traces/t-123"

    use_case = EnrichTraceCostUseCase(
        mcp_client=mock_mcp,
        tracer=mock_tracer,
        enabled=True,
    )

    result = await use_case.execute("Summary report", "t-123")

    mock_tracer.flush.assert_called_once()
    mock_mcp.call_tool.assert_awaited_once()
    assert "Summary report" in result
    assert "💸 **Cost:** $0.0236" in result
    assert "🔍 [View Trace](https://cloud.langfuse.com/project/p1/traces/t-123)" in result


@pytest.mark.asyncio
async def test_enrich_trace_cost_fallback_to_url_on_mcp_error():
    mock_mcp = AsyncMock(spec=MCPClientInterface)
    mock_mcp.call_tool.side_effect = Exception("Gateway unreachable")

    mock_tracer = MagicMock(spec=TracerInterface)
    mock_tracer.get_trace_url.return_value = "https://cloud.langfuse.com/project/p1/traces/t-123"

    use_case = EnrichTraceCostUseCase(
        mcp_client=mock_mcp,
        tracer=mock_tracer,
        enabled=True,
    )

    result = await use_case.execute("Summary report", "t-123")

    assert "Summary report" in result
    assert "💸 **Cost:**" not in result
    assert "🔍 [View Trace](https://cloud.langfuse.com/project/p1/traces/t-123)" in result


@pytest.mark.asyncio
async def test_notify_incident_report_enriches_summary():
    from lyoko.application.incidents.use_cases.notify import NotifyIncidentReportUseCase

    mock_chat_manager = AsyncMock()
    mock_enrich = AsyncMock(spec=EnrichTraceCostUseCase)
    mock_enrich.execute.return_value = "Enriched incident report"

    use_case = NotifyIncidentReportUseCase(
        chat_manager=mock_chat_manager,
        enrich_trace_cost=mock_enrich,
    )

    state = {
        "event_type": "alert",
        "alert_name": "HighMemory",
        "trace_id": "tr-abc",
        "root_cause": "leak",
        "action_taken": "restart",
    }

    await use_case.execute(state)

    mock_enrich.execute.assert_awaited_once()
    mock_chat_manager.broadcast_message.assert_awaited_once()
    _, kwargs = mock_chat_manager.broadcast_message.call_args
    assert kwargs["text"] == "Enriched incident report"


@pytest.mark.asyncio
async def test_process_chat_message_enriches_reply():
    from lyoko.application.chat.use_cases.process_message import ProcessChatMessageUseCase
    from lyoko.domain.models.chat import ChatUser, IncomingMessage

    mock_graph = MagicMock()
    mock_graph.ainvoke = AsyncMock(return_value={"reply": "Here is the cluster status."})

    mock_enrich = AsyncMock(spec=EnrichTraceCostUseCase)
    mock_enrich.execute.return_value = "Here is the cluster status.\n\n💸 **Cost:** $0.0010"

    use_case = ProcessChatMessageUseCase(
        graph_provider=lambda: mock_graph,
        enrich_trace_cost=mock_enrich,
    )

    msg = IncomingMessage(
        message_id="m1",
        chat_id="c1",
        user=ChatUser(user_id="u1", username="admin"),
        text="How is the cluster?",
    )

    result = await use_case.execute(msg)

    mock_enrich.execute.assert_awaited_once()
    assert result == "Here is the cluster status.\n\n💸 **Cost:** $0.0010"


@pytest.mark.asyncio
async def test_enrich_trace_cost_with_dict_mcp_response_and_args():
    mock_mcp = AsyncMock(spec=MCPClientInterface)
    mock_mcp.call_tool.return_value = {
        "status": "success",
        "content": '{"data": [{"traceId": "t-dict", "sum_totalCost": 0.0037}]}',
        "is_error": False,
    }

    mock_tracer = MagicMock(spec=TracerInterface)
    mock_tracer.get_trace_url.return_value = "https://cloud.langfuse.com/project/p1/traces/t-dict"

    use_case = EnrichTraceCostUseCase(
        mcp_client=mock_mcp,
        tracer=mock_tracer,
        enabled=True,
    )

    result = await use_case.execute("Diagnostic complete", "t-dict")

    mock_mcp.call_tool.assert_awaited_once()
    call_kwargs = mock_mcp.call_tool.call_args.kwargs
    name = call_kwargs.get("name")
    args = call_kwargs.get("arguments", {})

    assert name == "langfuse_queryMetrics"
    assert args["orderBy"] == [{"field": "totalCost", "direction": "desc"}]
    assert args["config"] == {"row_limit": 10}
    assert args["filters"][0]["value"] == "t-dict"
    assert "💸 **Cost:** $0.0037" in result
    assert "🔍 [View Trace](https://cloud.langfuse.com/project/p1/traces/t-dict)" in result


@pytest.mark.asyncio
async def test_enrich_trace_cost_with_parsed_dict_envelope():
    mock_mcp = AsyncMock(spec=MCPClientInterface)
    mock_mcp.call_tool.return_value = {
        "status": "success",
        "content": {"data": [{"traceId": "t-dict2", "totalCost": 0.015}]},
        "is_error": False,
    }

    use_case = EnrichTraceCostUseCase(mcp_client=mock_mcp, tracer=None, enabled=True)
    result = await use_case.execute("Action taken", "t-dict2")
    assert "💸 **Cost:** $0.0150" in result
