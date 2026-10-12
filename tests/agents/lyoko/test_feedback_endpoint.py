"""Tests for Feedback API router."""

from unittest.mock import AsyncMock

from fastapi.testclient import TestClient
from lyoko.domain.models.memory import MemoryEntry
from lyoko.infrastructure.api.server import create_app


def test_submit_feedback_endpoint_success():
    """Verify POST /api/v1/feedback saves memory successfully."""
    app = create_app()

    mock_memory_repo = AsyncMock()
    mock_memory_repo.save_memory.return_value = 101

    mock_embeddings = AsyncMock()
    mock_embeddings.embed_text.return_value = [0.1] * 1536

    app.state.memory_repository = mock_memory_repo
    app.state.embeddings_service = mock_embeddings

    client = TestClient(app)
    response = client.post(
        "/api/v1/feedback",
        json={
            "namespace": "monitoring",
            "service_name": "influxdb",
            "alert_name": "PodCrashLooping",
            "incident_pattern": "OOMKilled exit code 137 on startup",
            "operator_feedback": "Do not increase memory limit, check WAL compaction",
            "action_rule": "Truncate WAL first",
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert data["memory_id"] == 101
    assert mock_memory_repo.save_memory.called
    assert mock_embeddings.embed_text.called


def test_submit_feedback_endpoint_no_db():
    """Verify POST /api/v1/feedback returns 503 if db repository is not initialized."""
    app = create_app()
    app.state.memory_repository = None

    client = TestClient(app)
    response = client.post(
        "/api/v1/feedback",
        json={
            "namespace": "monitoring",
            "service_name": "influxdb",
            "incident_pattern": "OOMKilled",
            "operator_feedback": "Check disk",
        },
    )

    assert response.status_code == 503
    assert "PostgreSQL memory repository is not available" in response.json()["detail"]


def test_get_feedback_list():
    """Verify GET /api/v1/feedback lists memories."""
    app = create_app()
    mock_memory_repo = AsyncMock()
    mock_memory_repo.list_recent_memories.return_value = [
        MemoryEntry(
            id=1,
            namespace="media",
            service_name="radarr",
            incident_pattern="CrashLoop",
            operator_feedback="Check permissions on /downloads",
        )
    ]
    app.state.memory_repository = mock_memory_repo

    client = TestClient(app)
    response = client.get("/api/v1/feedback")

    assert response.status_code == 200
    data = response.json()
    assert data["count"] == 1
    assert data["memories"][0]["service_name"] == "radarr"
