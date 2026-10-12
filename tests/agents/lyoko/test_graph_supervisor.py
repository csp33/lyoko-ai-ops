"""Routing and the two branches of the LYOKO graph: chat and incident."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from lyoko.application.workflow.graph import create_lyoko_graph

from tests.agents.lyoko.fakes import (
    FakeMCPClient,
    ScriptedLLM,
)
from tests.agents.lyoko.routing_helpers import (
    _message,
)


@pytest.mark.asyncio
async def test_chat_branch_uses_supervisor_when_provided():
    mock_supervisor = MagicMock()
    mock_supervisor.coordinate = AsyncMock(return_value="Supervisor handled request.")

    workflow = create_lyoko_graph(
        mcp_client=FakeMCPClient(),
        llm=ScriptedLLM(route="CHAT"),
        supervisor=mock_supervisor,
    )

    result = await workflow.ainvoke(_message("what is the status of the network?"))
    assert result["reply"] == "Supervisor handled request."
    mock_supervisor.coordinate.assert_awaited_once()
    kwargs = mock_supervisor.coordinate.await_args.kwargs
    assert kwargs["prompt"] == "what is the status of the network?"
    assert kwargs["authorizer"] is not None
    assert "phase:chat" in kwargs["tags"]
    assert kwargs.get("tools") is None


@pytest.mark.asyncio
async def test_diagnose_uses_supervisor_when_provided():
    mock_supervisor = MagicMock()
    mock_supervisor.coordinate = AsyncMock(
        return_value=(
            "ROOT_CAUSE: Application gambling-song-staging is OutOfSync.\n"
            "ACTIONABLE: no\n"
            "PLAN: Ask a human to inspect the Argo CD sync diff."
        )
    )

    workflow = create_lyoko_graph(
        mcp_client=FakeMCPClient(),
        llm=ScriptedLLM(),
        supervisor=mock_supervisor,
    )

    result = await workflow.ainvoke(
        {
            "event_id": "incident-abc",
            "alert_name": "ArgoCDAppOutOfSync",
            "labels": {"alertname": "ArgoCDAppOutOfSync", "name": "gambling-song-staging"},
            "annotations": {"summary": "OutOfSync"},
        }
    )

    assert "OutOfSync" in result["root_cause"]
    assert result["requires_escalation"] is True
    mock_supervisor.coordinate.assert_awaited_once()
    kwargs = mock_supervisor.coordinate.await_args.kwargs
    assert "phase:diagnose" in kwargs["tags"]
    assert kwargs["authorizer"] is not None
    assert "ArgoCDAppOutOfSync" in kwargs["prompt"]
