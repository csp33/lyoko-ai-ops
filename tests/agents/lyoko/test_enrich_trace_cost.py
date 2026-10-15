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


@pytest.mark.asyncio
async def test_enrich_trace_cost_zero_when_no_llm_called():
    tracer_mock = MagicMock()
    tracer_mock.get_trace_url.return_value = "https://cloud.langfuse.com/traces/t-resolved"
    use_case = EnrichTraceCostUseCase(mcp_client=None, tracer=tracer_mock, enabled=True)
    result = await use_case.execute(
        "Alert resolved", "t-resolved", session_id="sess-100", is_llm_called=False
    )
    assert "💸 **Cost:** $0.0000" in result
    assert "🔍 [View Trace](https://cloud.langfuse.com/traces/t-resolved)" in result


@pytest.mark.asyncio
async def test_enrich_trace_cost_idempotent_no_duplicate():
    use_case = EnrichTraceCostUseCase(mcp_client=None, tracer=None, enabled=True)
    already_enriched = "Report body\n\n💸 **Cost:** $0.0100 · 🔍 [View Trace](https://cloud.langfuse.com/traces/t-1)"
    result = await use_case.execute(already_enriched, "t-1")
    assert result == already_enriched
    assert result.count("[View Trace]") == 1


@pytest.mark.asyncio
async def test_enrich_trace_cost_sub_cent_formatting():
    mock_mcp = AsyncMock(spec=MCPClientInterface)
    mock_mcp.call_tool.return_value = {
        "status": "success",
        "content": {"data": [{"traceId": "t-micro", "totalCost": 0.000035}]},
        "is_error": False,
    }
    use_case = EnrichTraceCostUseCase(mcp_client=mock_mcp, tracer=None, enabled=True)
    result = await use_case.execute("Short chat", "t-micro")
    assert "💸 **Cost:** <$0.0001" in result


@pytest.mark.asyncio
async def test_enrich_trace_cost_placeholder_when_langfuse_pending():
    mock_mcp = AsyncMock(spec=MCPClientInterface)
    mock_mcp.call_tool.return_value = {
        "status": "success",
        "content": {"data": []},
        "is_error": False,
    }
    use_case = EnrichTraceCostUseCase(mcp_client=mock_mcp, tracer=None, enabled=True)
    result = await use_case.execute("Action done", "t-pending")
    assert "💸 **Cost:** calculating..." in result


@pytest.mark.asyncio
async def test_update_message_cost_async_edits_telegram_message():
    mock_mcp = AsyncMock(spec=MCPClientInterface)
    mock_mcp.call_tool.return_value = {
        "status": "success",
        "content": {"data": [{"totalCost": 0.0436}]},
        "is_error": False,
    }
    mock_chat_manager = AsyncMock()
    use_case = EnrichTraceCostUseCase(mcp_client=mock_mcp, tracer=None, enabled=True)
    initial_text = (
        "Action done\n\n💸 **Cost:** calculating... · 🔍 [View Trace](https://example.com)"
    )

    await use_case.update_message_cost_async(
        chat_manager=mock_chat_manager,
        chat_id="-1001",
        message_id="42",
        text_with_placeholder=initial_text,
        trace_id="t-async",
        delay_seconds=0.01,
    )

    mock_chat_manager.edit_message.assert_awaited_once()
    _, kwargs = mock_chat_manager.edit_message.call_args
    assert kwargs["chat_id"] == "-1001"
    assert kwargs["message_id"] == "42"
    assert "💸 **Cost:** $0.0436" in kwargs["text"]
    assert "calculating..." not in kwargs["text"]
