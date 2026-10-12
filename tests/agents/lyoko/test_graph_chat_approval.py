"""Routing and the two branches of the LYOKO graph: chat and incident."""

from unittest.mock import AsyncMock

import pytest
from lyoko.application.safety.hitl.manager import ApprovalManager
from lyoko.application.workflow.graph import create_lyoko_graph
from lyoko.domain.interfaces.mcp import ToolAuthorizer

from tests.agents.lyoko.fakes import (
    FakeMCPClient,
    Operator,
    ScriptedLLM,
)
from tests.agents.lyoko.routing_helpers import (
    _alert,
    _message,
    _scale,
)


@pytest.mark.asyncio
async def test_chat_change_waits_for_approval_then_runs():
    manager = ApprovalManager()
    operator = Operator(manager, approve=True)
    chat = AsyncMock()
    chat.broadcast_approval_request = operator.broadcast_approval_request
    outcomes: list = []
    llm = ScriptedLLM(route="CHAT", chat=_scale(outcomes))
    graph = create_lyoko_graph(
        mcp_client=FakeMCPClient(), approval_manager=manager, chat_manager=chat, llm=llm
    )

    result = await graph.ainvoke(_message())

    assert outcomes == [None]
    assert result["reply"] == "Scaled sonarr to 2."
    assert [a["outcome"] for a in result["actions"]] == ["approved"]
    (request,) = operator.requests
    assert "resources_scale" in request.details
    assert "Chat request: scale sonarr to 2 replicas" in request.details
    assert '"replicas": 2' in request.details


@pytest.mark.asyncio
async def test_chat_approval_belongs_to_the_chat_session_and_fits_telegram_limits():
    manager = ApprovalManager()
    operator = Operator(manager, approve=True)
    chat = AsyncMock()
    chat.broadcast_approval_request = operator.broadcast_approval_request
    llm = ScriptedLLM(route="CHAT", chat=_scale([]))
    graph = create_lyoko_graph(
        mcp_client=FakeMCPClient(), approval_manager=manager, chat_manager=chat, llm=llm
    )

    await graph.ainvoke(_message())

    (request,) = operator.requests
    # Replying to the approval message continues the chat session, not a made-up one.
    assert request.session_id == "telegram-42-1760000000"
    assert request.incident_id == "chat-ab12cd34.1"
    assert request.chat_id == "42"
    assert len(f"approve:{request.incident_id}".encode()) <= 64


@pytest.mark.asyncio
async def test_chat_approval_is_sent_into_the_message_thread():
    manager = ApprovalManager()
    operator = Operator(manager, approve=True)
    chat = AsyncMock()
    chat.broadcast_approval_request = operator.broadcast_approval_request
    llm = ScriptedLLM(route="CHAT", chat=_scale([]))
    graph = create_lyoko_graph(
        mcp_client=FakeMCPClient(), approval_manager=manager, chat_manager=chat, llm=llm
    )

    await graph.ainvoke(_message(thread_id="321"))

    assert operator.thread_ids == ["321"]


@pytest.mark.asyncio
async def test_chat_change_denied_does_not_run():
    manager = ApprovalManager()
    operator = Operator(manager, approve=False, reason="not now")
    chat = AsyncMock()
    chat.broadcast_approval_request = operator.broadcast_approval_request
    outcomes: list = []
    llm = ScriptedLLM(route="CHAT", chat=_scale(outcomes))
    graph = create_lyoko_graph(
        mcp_client=FakeMCPClient(), approval_manager=manager, chat_manager=chat, llm=llm
    )

    result = await graph.ainvoke(_message())

    assert outcomes[0] is not None and "did not approve" in outcomes[0]
    assert [a["outcome"] for a in result["actions"]] == ["denied"]


@pytest.mark.asyncio
async def test_chat_runs_trusted_tools_without_asking(monkeypatch):
    monkeypatch.setattr(
        "lyoko.application.workflow.graph.settings.auto_approved_tools", ["resources_scale"]
    )
    manager = ApprovalManager()
    operator = Operator(manager, approve=False)
    chat = AsyncMock()
    chat.broadcast_approval_request = operator.broadcast_approval_request
    outcomes: list = []
    llm = ScriptedLLM(route="CHAT", chat=_scale(outcomes))
    graph = create_lyoko_graph(
        mcp_client=FakeMCPClient(), approval_manager=manager, chat_manager=chat, llm=llm
    )

    result = await graph.ainvoke(_message())

    assert outcomes == [None]
    assert operator.requests == []
    assert [a["outcome"] for a in result["actions"]] == ["auto_approved"]


@pytest.mark.asyncio
async def test_chat_reads_never_ask():
    manager = ApprovalManager()
    operator = Operator(manager, approve=False)
    chat = AsyncMock()
    chat.broadcast_approval_request = operator.broadcast_approval_request
    seen: list = []

    async def read(authorize: ToolAuthorizer) -> str:
        seen.append(await authorize("pods_get", {"name": "x"}))
        seen.append(await authorize("resources_list", {"kind": "Pod"}))
        return "listed"

    llm = ScriptedLLM(route="CHAT", chat=read)
    graph = create_lyoko_graph(
        mcp_client=FakeMCPClient(), approval_manager=manager, chat_manager=chat, llm=llm
    )

    result = await graph.ainvoke(_message())

    assert seen == [None, None]
    assert operator.requests == []
    assert result["actions"] == []


@pytest.mark.asyncio
async def test_chat_change_is_refused_when_there_is_no_approval_channel():
    outcomes: list = []
    llm = ScriptedLLM(route="CHAT", chat=_scale(outcomes))
    graph = create_lyoko_graph(mcp_client=FakeMCPClient(), llm=llm)

    result = await graph.ainvoke(_message())

    assert outcomes[0] is not None and "no approval channel" in outcomes[0]
    assert [a["outcome"] for a in result["actions"]] == ["refused"]


@pytest.mark.asyncio
async def test_approval_in_chat_does_not_block_another_event():
    """A chat run waiting for the operator must not stop an alert from being handled."""
    import asyncio

    manager = ApprovalManager()
    chat = AsyncMock()
    requests: list = []

    async def broadcast(request, message_thread_id=None, reply_to_message_id=None) -> None:
        requests.append(request)

    chat.broadcast_approval_request = broadcast
    chat_llm = ScriptedLLM(route="CHAT", chat=_scale([]))
    alert_llm = ScriptedLLM(diagnose="ROOT_CAUSE: x\nACTIONABLE: no\nPLAN: y")
    chat_graph = create_lyoko_graph(
        mcp_client=FakeMCPClient(), approval_manager=manager, chat_manager=chat, llm=chat_llm
    )
    alert_graph = create_lyoko_graph(mcp_client=FakeMCPClient(), llm=alert_llm)

    waiting = asyncio.create_task(chat_graph.ainvoke(_message()))
    await asyncio.sleep(0.05)
    assert not waiting.done()

    await alert_graph.ainvoke(_alert())
    assert not waiting.done()

    from lyoko.domain.models.chat import ApprovalResponse

    manager.resolve_approval(
        ApprovalResponse(incident_id=requests[0].incident_id, approved=True, user_id="admin")
    )
    result = await asyncio.wait_for(waiting, timeout=2)
    assert result["reply"] == "Scaled sonarr to 2."
