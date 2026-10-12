"""Routing and the two branches of the LYOKO graph: chat and incident."""

from unittest.mock import AsyncMock

import pytest
from lyoko.application.workflow.graph import create_lyoko_graph
from lyoko.domain.interfaces.mcp import ToolAuthorizer

from tests.agents.lyoko.fakes import (
    FakeMCPClient,
    ScriptedLLM,
)
from tests.agents.lyoko.routing_helpers import (
    _alert,
    _message,
)


@pytest.mark.asyncio
async def test_alert_goes_straight_to_the_incident_branch_without_asking_the_router():
    llm = ScriptedLLM(
        diagnose="ROOT_CAUSE: x\nACTIONABLE: no\nPLAN: y",
        route="CHAT",
    )
    graph = create_lyoko_graph(mcp_client=FakeMCPClient(), llm=llm)

    await graph.ainvoke(_alert())

    assert llm.calls == ["diagnose"]


@pytest.mark.asyncio
async def test_alert_without_an_event_type_is_still_an_alert():
    """States written before the router existed carry no event_type."""
    state = _alert()
    del state["event_type"]
    llm = ScriptedLLM(diagnose="ROOT_CAUSE: x\nACTIONABLE: no\nPLAN: y")
    graph = create_lyoko_graph(mcp_client=FakeMCPClient(), llm=llm)

    await graph.ainvoke(state)

    assert llm.calls == ["diagnose"]


@pytest.mark.asyncio
async def test_message_the_router_calls_chat_is_answered_in_one_run():
    llm = ScriptedLLM(route="CHAT", chat="Your printer is at 192.168.1.50.")
    graph = create_lyoko_graph(mcp_client=FakeMCPClient(), llm=llm)

    result = await graph.ainvoke(_message("what IP does the printer have?"))

    assert result["reply"] == "Your printer is at 192.168.1.50."
    assert llm.calls == ["route", "chat"]
    assert llm.prompts["route"] == "what IP does the printer have?"
    assert llm.prompts["chat"] == "what IP does the printer have?"


@pytest.mark.asyncio
async def test_message_the_router_calls_an_incident_runs_the_incident_branch():
    llm = ScriptedLLM(
        route="INCIDENT",
        diagnose="ROOT_CAUSE: The database credentials are invalid.\nACTIONABLE: no\nPLAN: rotate",
    )
    chat = AsyncMock()
    graph = create_lyoko_graph(mcp_client=FakeMCPClient(), chat_manager=chat, llm=llm)

    result = await graph.ainvoke(_message("sonarr keeps crashing, fix it"))

    assert llm.calls == ["route", "diagnose"]
    assert "sonarr keeps crashing, fix it" in llm.prompts["diagnose"]
    assert "Incident Report" in result["reply"]
    assert "database credentials" in result["reply"]
    # The Telegram handler replies with the report. Broadcasting it too would send it twice.
    chat.broadcast_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_alert_report_is_broadcast_and_not_returned_as_a_reply():
    llm = ScriptedLLM(diagnose="ROOT_CAUSE: x\nACTIONABLE: no\nPLAN: y")
    chat = AsyncMock()
    graph = create_lyoko_graph(mcp_client=FakeMCPClient(), chat_manager=chat, llm=llm)

    result = await graph.ainvoke(_alert())

    chat.broadcast_message.assert_awaited_once()
    assert "reply" not in result


@pytest.mark.parametrize("answer", ["", "maybe?", "Not sure, honestly."])
@pytest.mark.asyncio
async def test_unclear_router_answer_falls_back_to_chat(answer):
    llm = ScriptedLLM(route=answer, chat="ok")
    graph = create_lyoko_graph(mcp_client=FakeMCPClient(), llm=llm)

    result = await graph.ainvoke(_message())

    assert result["reply"] == "ok"
    assert llm.calls == ["route", "chat"]


@pytest.mark.asyncio
async def test_router_failure_falls_back_to_chat():
    async def broken(_: ToolAuthorizer) -> str:
        raise RuntimeError("router model unavailable")

    class FailingRouter(ScriptedLLM):
        async def chat(self, *args, **kwargs):
            if "phase:route" in (kwargs.get("tags") or []):
                raise RuntimeError("router model unavailable")
            return await super().chat(*args, **kwargs)

    llm = FailingRouter(chat="answered anyway")
    graph = create_lyoko_graph(mcp_client=FakeMCPClient(), llm=llm)

    result = await graph.ainvoke(_message())

    assert result["reply"] == "answered anyway"


@pytest.mark.asyncio
async def test_message_without_llm_says_so():
    graph = create_lyoko_graph(mcp_client=FakeMCPClient(), llm=None)

    result = await graph.ainvoke(_message("ping"))

    assert "LLM provider not configured" in result["reply"]
    assert "ping" in result["reply"]


@pytest.mark.asyncio
async def test_chat_failure_is_reported_as_the_reply():
    class Failing(ScriptedLLM):
        async def chat(self, *args, **kwargs):
            if "phase:chat" in (kwargs.get("tags") or []):
                raise RuntimeError("OpenAI API rate limit exceeded")
            return await super().chat(*args, **kwargs)

    graph = create_lyoko_graph(mcp_client=FakeMCPClient(), llm=Failing(route="CHAT"))

    result = await graph.ainvoke(_message())

    assert "⚠️ Error processing your question:" in result["reply"]
    assert "OpenAI API rate limit exceeded" in result["reply"]
