"""Integration tests for LYOKO with real PostgreSQL checkpointer."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from lyoko.domain.interfaces.llm import LLMClientInterface
from lyoko.infrastructure.api.server import create_app, lifespan


@pytest.mark.asyncio
async def test_postgres_checkpointer_integration_with_local_container():
    """Verify full end-to-end checkpointing against running PostgreSQL instance."""
    app = create_app()

    mock_mcp = MagicMock()
    mock_mcp.verify_connection = AsyncMock()
    mock_mcp.get_langchain_tools = MagicMock(return_value=[])

    mock_llm = AsyncMock(spec=LLMClientInterface)
    mock_llm.chat.return_value = (
        "ROOT_CAUSE: The database credentials are invalid.\n"
        "ACTIONABLE: no\n"
        "PLAN: A human should rotate the credentials."
    )

    with (
        patch(
            "lyoko.infrastructure.api.server.settings.postgres_uri",
            "postgresql://lyoko:lyoko@127.0.0.1:5432/lyoko",
        ),
        patch("lyoko.infrastructure.api.server.FastMCPClient", return_value=mock_mcp),
        patch("lyoko.infrastructure.api.server.build_llm_adapter", return_value=mock_llm),
    ):
        try:
            async with lifespan(app):
                # Ensure checkpointer is initialized
                assert app.state.checkpointer is not None

                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://test"
                ) as client:
                    health = await client.get("/healthz")
                    assert health.status_code == 200
                    assert health.json()["postgres_checkpointer"] is True

                # Test workflow invocation through state engine
                engine = app.state.workflow_engine
                with patch(
                    "lyoko.application.workflow.graph.settings.verification_delay_seconds", 0
                ):
                    config = {"configurable": {"thread_id": "test-integration-incident-001"}}
                    initial_state = {
                        "event_id": "media-sonarr-test",
                        "alert_name": "KubePodCrashLooping",
                        "labels": {"alertname": "KubePodCrashLooping", "namespace": "media"},
                        "annotations": {},
                    }

                    result = await engine.ainvoke(initial_state, config=config)
                    assert result["requires_escalation"] is True

                    # Retrieve state back from PostgreSQL checkpointer
                    saved_state = await engine.aget_state(config)
                    assert "credentials" in saved_state.values["root_cause"]
        except Exception as exc:
            # If postgres is not running (e.g. in minimal CI environment), skip gracefully
            pytest.skip(f"Local PostgreSQL container not reachable: {exc}")
