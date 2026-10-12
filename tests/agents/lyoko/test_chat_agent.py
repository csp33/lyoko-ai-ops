"""Unit tests for LYOKO InteractiveChatAgent: chat messages entering the graph."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
from lyoko.application.chat.agent import InteractiveChatAgent
from lyoko.application.chat.sessions import ChatSessionTracker
from lyoko.application.workflow.graph import create_lyoko_graph
from lyoko.domain.models.chat import ChatUser, IncomingMessage

from tests.agents.lyoko.fakes import FakeMCPClient, ScriptedLLM


def _msg(
    text: str,
    message_id: str = "1",
    reply_to: str | None = None,
    thread_id: str | None = None,
) -> IncomingMessage:
    return IncomingMessage(
        message_id=message_id,
        chat_id="42",
        user=ChatUser(user_id="42", username="admin"),
        text=text,
        reply_to_message_id=reply_to,
        message_thread_id=thread_id,
    )


def _agent(llm, *, tracer=None, tracker=None) -> InteractiveChatAgent:
    graph = create_lyoko_graph(mcp_client=FakeMCPClient(), llm=llm)
    return InteractiveChatAgent(lambda: graph, tracer=tracer, session_tracker=tracker)


@pytest.mark.asyncio
async def test_message_is_answered_by_the_graph():
    llm = ScriptedLLM(route="CHAT", chat="All 5 pods are healthy in default namespace.")
    agent = _agent(llm)

    reply = await agent.handle_message(_msg("How is the cluster?"))

    assert reply == "All 5 pods are healthy in default namespace."
    assert llm.prompts["chat"] == "How is the cluster?"


@pytest.mark.asyncio
async def test_incident_message_gets_the_report_as_the_reply():
    llm = ScriptedLLM(
        route="INCIDENT",
        diagnose="ROOT_CAUSE: Disk full.\nACTIONABLE: no\nPLAN: free space",
    )
    agent = _agent(llm)

    reply = await agent.handle_message(_msg("the NAS is unreachable"))

    assert "Incident Report" in reply
    assert "Disk full." in reply


@pytest.mark.asyncio
async def test_graph_errors_become_an_error_reply():
    graph = MagicMock()
    graph.ainvoke = AsyncMock(side_effect=RuntimeError("checkpointer down"))
    agent = InteractiveChatAgent(lambda: graph)

    reply = await agent.handle_message(_msg("Can you restart nginx?"))

    assert "⚠️ Error processing your question:" in reply
    assert "checkpointer down" in reply


@pytest.mark.asyncio
async def test_without_llm_the_reply_says_so():
    agent = _agent(None)

    reply = await agent.handle_message(_msg("Ping"))

    assert "LLM provider not configured" in reply


@pytest.mark.asyncio
async def test_each_message_starts_a_trace_with_session_user_and_tags():
    tracer = MagicMock()
    tracer.get_trace_config.return_value = {"tags": ["from-tracer"]}
    graph = MagicMock()
    graph.ainvoke = AsyncMock(return_value={"reply": "ok"})
    agent = InteractiveChatAgent(lambda: graph, tracer=tracer)

    await agent.handle_message(_msg("How is the cluster?", message_id="77"))

    kwargs = tracer.get_trace_config.call_args.kwargs
    assert kwargs["session_id"].startswith("telegram-42-")
    assert kwargs["user_id"] == "42"
    assert kwargs["trace_name"] == "telegram-chat-interaction"
    assert "interactive-chat" in kwargs["tags"]
    assert "chat:42" in kwargs["tags"]
    assert kwargs["metadata"] == {"chat_id": "42", "username": "admin", "message_id": "77"}


@pytest.mark.asyncio
async def test_graph_run_carries_event_identity_and_a_checkpoint_thread():
    graph = MagicMock()
    graph.ainvoke = AsyncMock(return_value={"reply": "ok"})
    agent = InteractiveChatAgent(lambda: graph)

    await agent.handle_message(_msg("hello"))

    state = graph.ainvoke.call_args.args[0]
    config = graph.ainvoke.call_args.kwargs["config"]
    assert state["event_type"] == "message"
    assert state["text"] == "hello"
    assert state["chat_id"] == "42"
    assert state["message_thread_id"] is None
    assert state["session_id"].startswith("telegram-42-")
    # Short enough for "approve:<event>.<n>" to fit Telegram's 64 byte callback data.
    assert state["event_id"].startswith("chat-")
    assert len(f"approve:{state['event_id']}.99".encode()) <= 64
    assert config["configurable"]["thread_id"] == state["event_id"]
    assert config["run_name"] == "telegram-chat-interaction"


@pytest.mark.asyncio
async def test_message_thread_id_reaches_graph_state():
    """A message in a forum topic must carry its thread so approvals reply in-topic."""
    graph = MagicMock()
    graph.ainvoke = AsyncMock(return_value={"reply": "ok"})
    agent = InteractiveChatAgent(lambda: graph)

    await agent.handle_message(_msg("hello", thread_id="321"))

    state = graph.ainvoke.call_args.args[0]
    assert state["message_thread_id"] == "321"


@pytest.mark.asyncio
async def test_every_message_gets_its_own_event_id():
    graph = MagicMock()
    graph.ainvoke = AsyncMock(return_value={"reply": "ok"})
    agent = InteractiveChatAgent(lambda: graph)

    await agent.handle_message(_msg("one", message_id="1"))
    await agent.handle_message(_msg("two", message_id="2"))

    first, second = (c.args[0]["event_id"] for c in graph.ainvoke.call_args_list)
    assert first != second


@pytest.mark.asyncio
async def test_graph_is_looked_up_on_every_message():
    """The composition root replaces the graph at startup, so it must not be cached."""
    graphs = [MagicMock(ainvoke=AsyncMock(return_value={"reply": "old"}))]
    agent = InteractiveChatAgent(lambda: graphs[-1])
    await agent.handle_message(_msg("a"))

    graphs.append(MagicMock(ainvoke=AsyncMock(return_value={"reply": "new"})))

    assert await agent.handle_message(_msg("b")) == "new"


def _clocked_agent(now: list[datetime]) -> tuple[InteractiveChatAgent, ScriptedLLM, list[str]]:
    llm = ScriptedLLM(route="CHAT", chat="ok")
    sessions: list[str] = []
    graph = create_lyoko_graph(mcp_client=FakeMCPClient(), llm=llm)
    inner = graph.ainvoke

    async def spy(state, config=None):
        sessions.append(state["session_id"])
        return await inner(state, config=config)

    graph.ainvoke = spy
    tracker = ChatSessionTracker(idle_timeout_seconds=900, clock=lambda: now[0])
    return InteractiveChatAgent(lambda: graph, session_tracker=tracker), llm, sessions


@pytest.mark.asyncio
async def test_new_command_starts_fresh_session_without_calling_llm():
    """/new rotates the session and does not spend an LLM call."""
    now = [datetime(2026, 10, 2, 20, 0, 0, tzinfo=UTC)]
    agent, llm, sessions = _clocked_agent(now)

    await agent.handle_message(_msg("first topic"))
    now[0] += timedelta(seconds=30)
    reply = await agent.handle_message(_msg("/new"))
    assert "new session" in reply.lower()
    assert llm.calls == ["route", "chat"]

    now[0] += timedelta(seconds=30)
    await agent.handle_message(_msg("second topic"))

    assert len(sessions) == 2
    assert sessions[0] != sessions[1]


@pytest.mark.asyncio
async def test_new_command_with_bot_mention_is_recognized():
    now = [datetime(2026, 10, 2, 20, 0, 0, tzinfo=UTC)]
    agent, llm, _ = _clocked_agent(now)

    reply = await agent.handle_message(_msg("/new@lyoko_bot"))

    assert "new session" in reply.lower()
    assert llm.calls == []


@pytest.mark.asyncio
async def test_reply_to_linked_alert_message_uses_incident_session():
    """Replying to an alert message continues that incident's session."""
    now = [datetime(2026, 10, 2, 20, 0, 0, tzinfo=UTC)]
    agent, _, sessions = _clocked_agent(now)
    agent.session_tracker.link_message("42", "900", "incident-abc123")

    await agent.handle_message(_msg("why did it die?", message_id="901", reply_to="900"))
    assert sessions[-1] == "incident-abc123"

    # A follow-up without an explicit reply stays in the incident session.
    now[0] += timedelta(minutes=2)
    await agent.handle_message(_msg("and the logs?", message_id="902"))
    assert sessions[-1] == "incident-abc123"


@pytest.mark.asyncio
async def test_on_status_is_in_config_not_in_state():
    """on_status callback must be in config['configurable'] and NOT in state to prevent checkpointer serialization errors."""
    graph = MagicMock()
    graph.ainvoke = AsyncMock(return_value={"reply": "ok"})
    agent = InteractiveChatAgent(lambda: graph)

    async def dummy_on_status(text: str) -> None:
        pass

    await agent.handle_message(_msg("hello"), on_status=dummy_on_status)

    state = graph.ainvoke.call_args.args[0]
    config = graph.ainvoke.call_args.kwargs["config"]
    assert "on_status" not in state
    assert config["configurable"]["on_status"] is dummy_on_status


@pytest.mark.asyncio
async def test_chat_with_checkpointer_and_on_status_serialization():
    """Full workflow execution with checkpointer and on_status does not fail with msgpack TypeError."""
    from langgraph.checkpoint.memory import MemorySaver
    from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

    saver = MemorySaver(serde=JsonPlusSerializer())
    llm = ScriptedLLM(route="CHAT", chat="Pong")
    graph = create_lyoko_graph(mcp_client=FakeMCPClient(), checkpointer=saver, llm=llm)
    agent = InteractiveChatAgent(lambda: graph)

    async def status_collector(_text: str) -> None:
        pass

    reply = await agent.handle_message(_msg("Ping"), on_status=status_collector)
    assert reply == "Pong"
