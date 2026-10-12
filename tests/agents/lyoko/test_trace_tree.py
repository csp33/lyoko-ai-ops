"""One event, one readable trace: real LangGraph + ReAct agent, recorded like a tracing backend."""

from typing import Any
from unittest.mock import AsyncMock

import pytest
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
from lyoko.application.workflow.graph import create_lyoko_graph
from lyoko.infrastructure.llm.openai import OpenAILLMAdapter
from lyoko.infrastructure.mcp.client import FastMCPClient


class _ToolCallingFakeModel(GenericFakeChatModel):
    """Scripted chat model that accepts ``bind_tools`` like ChatOpenAI does."""

    def bind_tools(self, tools: Any, **kwargs: Any) -> "_ToolCallingFakeModel":
        return self


class _Tree(BaseCallbackHandler):
    """Collects every run of the trace with its parent, as Langfuse would receive them."""

    def __init__(self) -> None:
        self.names: dict[Any, str] = {}
        self.parents: dict[Any, Any] = {}

    def _start(self, run_id: Any, parent_run_id: Any, name: str | None) -> None:
        self.names[run_id] = name or ""
        self.parents[run_id] = parent_run_id

    def on_chain_start(self, serialized, inputs, *, run_id, parent_run_id=None, name=None, **kw):
        self._start(run_id, parent_run_id, name)

    def on_tool_start(self, serialized, input_str, *, run_id, parent_run_id=None, **kwargs):
        self._start(run_id, parent_run_id, serialized.get("name"))

    def on_chat_model_start(
        self, serialized, messages, *, run_id, parent_run_id=None, name=None, **kw
    ):
        self._start(run_id, parent_run_id, name or "chat-model")

    def ancestors(self, name: str) -> list[str]:
        (run_id,) = [r for r, n in self.names.items() if n == name]
        chain = []
        parent = self.parents[run_id]
        while parent is not None:
            chain.append(self.names[parent])
            parent = self.parents[parent]
        return chain

    def roots(self) -> list[str]:
        return [self.names[r] for r, p in self.parents.items() if p is None]


def _tool_call(tool_name: str, arguments: dict) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[
            {
                "name": "gateway_call_tool",
                "args": {"tool_name": tool_name, "arguments": arguments},
                "id": "call_1",
            }
        ],
    )


@pytest.mark.asyncio
async def test_chat_message_produces_a_single_trace_with_named_spans():
    model = _ToolCallingFakeModel(
        messages=iter(
            [
                AIMessage(content="CHAT"),  # router
                _tool_call("pods_log", {"name": "radarr"}),  # chat agent, step 1
                AIMessage(content="Radarr is fine."),  # chat agent, step 2
            ]
        )
    )
    llm = OpenAILLMAdapter(api_key="sk-test")
    llm._client = model
    mcp = FastMCPClient(server_url="http://x/mcp", token="")
    mcp.call_tool = AsyncMock(return_value={"lines": ["ok"]})
    graph = create_lyoko_graph(mcp_client=mcp, llm=llm)
    tree = _Tree()

    result = await graph.ainvoke(
        {
            "event_type": "message",
            "event_id": "chat-1",
            "session_id": "telegram-42-1",
            "chat_id": "42",
            "text": "how is radarr?",
            "labels": {},
            "annotations": {},
        },
        config={"callbacks": [tree], "run_name": "telegram-chat-interaction"},
    )

    assert result["reply"] == "Radarr is fine."
    mcp.call_tool.assert_awaited_once_with("pods_log", {"name": "radarr"})

    # One trace: the graph is the only run without a parent.
    assert tree.roots() == ["telegram-chat-interaction"]

    # Each step has a name that says what it is, under the node that ran it.
    assert tree.ancestors("route-llm")[:2] == ["route", "telegram-chat-interaction"]
    agent_chain = tree.ancestors("chat-agent")
    assert agent_chain[:2] == ["coordinator", "telegram-chat-interaction"]

    # The gateway call is visible by its real tool name, nested inside the agent run.
    assert "mcp:pods_log" in tree.names.values()
    mcp_chain = tree.ancestors("mcp:pods_log")
    assert mcp_chain[0] == "gateway_call_tool"
    assert "chat-agent" in mcp_chain
    assert "coordinator" in mcp_chain
    assert mcp_chain[-1] == "telegram-chat-interaction"


@pytest.mark.asyncio
async def test_alert_incident_phases_are_children_of_the_one_trace():
    model = _ToolCallingFakeModel(
        messages=iter(
            [
                _tool_call("pods_get", {"name": "radarr"}),
                AIMessage(content="ROOT_CAUSE: OOM.\nACTIONABLE: no\nPLAN: raise memory"),
            ]
        )
    )
    llm = OpenAILLMAdapter(api_key="sk-test")
    llm._client = model
    mcp = FastMCPClient(server_url="http://x/mcp", token="")
    mcp.call_tool = AsyncMock(return_value={"status": "CrashLoopBackOff"})
    graph = create_lyoko_graph(mcp_client=mcp, llm=llm)
    tree = _Tree()

    await graph.ainvoke(
        {
            "event_type": "alert",
            "event_id": "incident-1",
            "session_id": "incident-1",
            "alert_name": "KubePodCrashLooping",
            "labels": {"alertname": "KubePodCrashLooping"},
            "annotations": {},
        },
        config={"callbacks": [tree], "run_name": "lyoko-KubePodCrashLooping-1"},
    )

    assert tree.roots() == ["lyoko-KubePodCrashLooping-1"]
    assert tree.ancestors("diagnose-agent")[:2] == ["coordinator", "lyoko-KubePodCrashLooping-1"]
    assert "mcp:pods_get" in tree.names.values()
    assert "route-llm" not in tree.names.values()
