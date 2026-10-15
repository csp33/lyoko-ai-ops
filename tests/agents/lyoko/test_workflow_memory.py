"""Tests for LYOKO workflow integration with Hermes memory delegation."""

from unittest.mock import AsyncMock, patch

import pytest
from lyoko.application.workflow.graph import create_lyoko_graph

from tests.agents.lyoko.fakes import (
    DIAGNOSIS_ACTIONABLE,
    FakeMCPClient,
    ScriptedLLM,
)


@pytest.mark.asyncio
async def test_workflow_diagnose_delegates_memory_to_hermes():
    """Verify diagnose does not inject pgvector when memory is delegated to Hermes."""
    llm = ScriptedLLM(
        diagnose=DIAGNOSIS_ACTIONABLE,
        remediate="RESULT: Took action following operator rule.",
        verify="RESOLVED\nPod is stable.",
    )
    mock_memory_repo = AsyncMock()
    mock_embeddings = AsyncMock()

    with patch("lyoko.application.workflow.graph.settings.verification_delay_seconds", 0):
        workflow = create_lyoko_graph(
            mcp_client=FakeMCPClient(),
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

        result = await workflow.ainvoke(initial_state)

        assert not mock_memory_repo.search_memories.called
        assert not mock_embeddings.embed_text.called
        assert "PRIOR OPERATOR FEEDBACK & LESSONS LEARNED" not in llm.prompts.get("diagnose", "")
        assert result.get("matched_memories") == []


@pytest.mark.asyncio
async def test_workflow_chat_delegates_memory_to_hermes():
    """Chat turn does not inject pgvector into prompt as memory is delegated to Hermes."""
    llm = ScriptedLLM(route="CHAT", chat="I will check.")
    mock_memory_repo = AsyncMock()
    mock_embeddings = AsyncMock()

    workflow = create_lyoko_graph(
        mcp_client=FakeMCPClient(),
        llm=llm,
        memory_repository=mock_memory_repo,
        embeddings_service=mock_embeddings,
    )

    result = await workflow.ainvoke(
        {
            "event_id": "chat-hermes-1",
            "event_type": "message",
            "text": "status check",
            "chat_id": "42",
        }
    )

    assert not mock_memory_repo.search_memories.called
    assert not mock_embeddings.embed_text.called
    assert "BACKGROUND CONTEXT" not in llm.prompts.get("chat", "")
    assert result["reply"] == "I will check."
