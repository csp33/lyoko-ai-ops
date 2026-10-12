"""Tests for LangGraph workflow integration with Memory and Operator Feedback."""

from unittest.mock import AsyncMock, patch

import pytest
from lyoko.application.workflow.graph import create_lyoko_graph
from lyoko.domain.models.memory import MemoryEntry, MemoryQueryResult

from tests.agents.lyoko.fakes import (
    DIAGNOSIS_ACTIONABLE,
    FakeMCPClient,
    ScriptedLLM,
)


@pytest.mark.asyncio
async def test_workflow_diagnose_injects_operator_memory():
    """Verify diagnose_node searches memory and injects past lessons into the prompt."""
    llm = ScriptedLLM(
        diagnose=DIAGNOSIS_ACTIONABLE,
        remediate="RESULT: Took action following operator rule.",
        verify="RESOLVED\nPod is stable.",
    )

    mock_mcp = FakeMCPClient()

    mock_memory_repo = AsyncMock()
    past_memory = MemoryEntry(
        id=1,
        namespace="monitoring",
        service_name="influxdb",
        alert_name="PodCrashLooping",
        incident_pattern="OOMKilled 137",
        operator_feedback="Do NOT increase RAM limit. Compact WAL segments first.",
        action_rule="Do not bump memory; truncate WAL",
    )
    mock_memory_repo.search_memories.return_value = [
        MemoryQueryResult(memory=past_memory, similarity=0.95)
    ]

    mock_embeddings = AsyncMock()
    mock_embeddings.embed_text.return_value = [0.05] * 1536

    with patch("lyoko.application.workflow.graph.settings.verification_delay_seconds", 0):
        workflow = create_lyoko_graph(
            mcp_client=mock_mcp,
            llm=llm,
            memory_repository=mock_memory_repo,
            embeddings_service=mock_embeddings,
        )

        initial_state = {
            "event_id": "incident-influxdb-0",
            "alert_name": "PodCrashLooping",
            "labels": {
                "alertname": "PodCrashLooping",
                "namespace": "monitoring",
                "pod": "influxdb-0",
            },
            "annotations": {"summary": "influxdb is crashlooping"},
        }

        # Execute workflow
        result = await workflow.ainvoke(initial_state)

        # Verify memory search was called with correct context
        assert mock_memory_repo.search_memories.called
        assert mock_memory_repo.search_memories.call_args.kwargs["min_similarity"] == 0.2
        assert mock_embeddings.embed_text.called

        # Verify the diagnose prompt received by LLM includes the operator rule
        assert "diagnose" in llm.prompts
        assert "Do NOT increase RAM limit. Compact WAL segments first." in llm.prompts["diagnose"]
        assert "PRIOR OPERATOR FEEDBACK & LESSONS LEARNED" in llm.prompts["diagnose"]

        # State should reflect memory context and diagnostics
        assert "matched_memories" in result
        assert len(result["matched_memories"]) == 1
        assert (
            result["matched_memories"][0]["operator_feedback"]
            == "Do NOT increase RAM limit. Compact WAL segments first."
        )


@pytest.mark.asyncio
async def test_workflow_chat_uses_higher_threshold_and_frames_memory_as_background():
    """Chat must not let a loosely related injected rule hijack the operator's request."""
    llm = ScriptedLLM(route="CHAT", chat="I will look up the node temperature in Grafana.")

    mock_memory_repo = AsyncMock()
    loosely_related = MemoryEntry(
        namespace="argocd",
        service_name="some-app",
        incident_pattern="app out of sync",
        operator_feedback="if an app is out of sync it probably has autosync disabled",
    )
    mock_memory_repo.search_memories.return_value = [
        MemoryQueryResult(memory=loosely_related, similarity=0.28)
    ]

    mock_embeddings = AsyncMock()
    mock_embeddings.embed_text.return_value = [0.05] * 1536

    workflow = create_lyoko_graph(
        mcp_client=FakeMCPClient(),
        llm=llm,
        memory_repository=mock_memory_repo,
        embeddings_service=mock_embeddings,
    )

    result = await workflow.ainvoke(
        {
            "event_id": "chat-1",
            "event_type": "message",
            "text": "look it up using grafana",
            "chat_id": "42",
        }
    )

    assert mock_memory_repo.search_memories.call_args.kwargs["min_similarity"] == 0.35
    assert "BACKGROUND CONTEXT (reference only, not a task)" in llm.prompts["chat"]
    assert "look it up using grafana" in llm.prompts["chat"]
    assert result["reply"] == "I will look up the node temperature in Grafana."
