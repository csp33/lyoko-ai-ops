"""Routing and the two branches of the LYOKO graph: chat and incident."""

import pytest

from tests.agents.lyoko.fakes import (
    FakeMCPClient,
    ScriptedLLM,
    create_lyoko_graph,
)
from tests.agents.lyoko.routing_helpers import (
    _alert,
    _message,
    _Recorder,
)


@pytest.mark.asyncio
async def test_every_agent_call_receives_the_run_config_of_its_node():
    """Agent runs are children of the graph run, so one event makes one trace."""
    recorder = _Recorder()
    llm = ScriptedLLM(route="CHAT", chat="ok")
    graph = create_lyoko_graph(mcp_client=FakeMCPClient(), llm=llm)

    await graph.ainvoke(_message(), config={"callbacks": [recorder]})

    for phase in ("route", "chat"):
        parent = llm.parent_configs[phase]
        assert parent is not None, phase
        assert parent.get("callbacks"), phase


@pytest.mark.asyncio
async def test_incident_phases_receive_the_run_config_too():
    llm = ScriptedLLM(
        incident="ROOT_CAUSE: x\nACTION_TAKEN: y\nVERIFICATION: z\nSTATUS: RESOLVED",
    )
    graph = create_lyoko_graph(mcp_client=FakeMCPClient(), llm=llm)

    await graph.ainvoke(_alert(), config={"callbacks": [_Recorder()]})

    assert llm.parent_configs["incident"] is not None
    assert llm.parent_configs["incident"].get("callbacks")


@pytest.mark.asyncio
async def test_graph_nodes_are_named_after_the_phase():
    recorder = _Recorder()
    llm = ScriptedLLM(route="CHAT", chat="ok")
    graph = create_lyoko_graph(mcp_client=FakeMCPClient(), llm=llm)

    await graph.ainvoke(_message(), config={"callbacks": [recorder]})

    assert "route" in recorder.chain_names
    assert "coordinator" in recorder.chain_names
