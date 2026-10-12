"""Unit tests for LYOKO FastAPI Alertmanager webhook receiver."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from lyoko.infrastructure.api.controller import create_webhook_router


@pytest.fixture(autouse=True)
def fast_alert_guard(monkeypatch):
    monkeypatch.setattr("lyoko.config.settings.alert_debounce_seconds", 0.0)
    monkeypatch.setattr("lyoko.config.settings.alert_dedup_cooldown_seconds", 0)
    monkeypatch.setattr("lyoko.config.settings.alert_storm_threshold", 8)
    monkeypatch.setattr("lyoko.config.settings.alert_storm_window_seconds", 60)


@pytest.fixture
def mock_workflow():
    workflow = MagicMock()
    workflow.ainvoke = AsyncMock()
    return workflow


@pytest.fixture
def test_client(mock_workflow):
    app = FastAPI()
    router = create_webhook_router(mock_workflow)
    app.include_router(router)
    return TestClient(app)


def test_alertmanager_webhook_firing_alert(test_client, mock_workflow):
    payload = {
        "status": "firing",
        "alerts": [
            {
                "status": "firing",
                "labels": {
                    "alertname": "KubePodCrashLooping",
                    "namespace": "media",
                    "pod": "radarr-79dfb8bf7-x82k",
                    "deployment": "radarr",
                },
                "annotations": {
                    "summary": "Pod radarr is crashlooping",
                },
                "fingerprint": "abc12345",
            }
        ],
    }

    response = test_client.post("/webhook/alertmanager", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "accepted"
    assert "Processing 1 alerts" in data["message"]
    mock_workflow.ainvoke.assert_called_once()
    _, kwargs = mock_workflow.ainvoke.call_args
    config = kwargs.get("config", {})
    assert config.get("configurable") == {"thread_id": "incident-abc12345"}
    assert "lyoko" in config.get("tags", [])
    assert config.get("metadata", {}).get("pod_name") == "radarr-79dfb8bf7-x82k"


def test_alertmanager_webhook_ignored_resolved(test_client, mock_workflow):
    payload = {
        "status": "resolved",
        "alerts": [
            {
                "status": "resolved",
                "labels": {
                    "alertname": "KubePodCrashLooping",
                    "namespace": "media",
                    "pod": "radarr-xxx",
                },
            }
        ],
    }

    response = test_client.post("/webhook/alertmanager", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "accepted"


def test_alertmanager_webhook_invalid_json(test_client):
    response = test_client.post(
        "/webhook/alertmanager",
        content="not-json",
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 400


def test_alertmanager_webhook_with_tracer(mock_workflow):
    mock_tracer = MagicMock()
    mock_callback = MagicMock()
    mock_tracer.get_trace_config.return_value = {
        "callbacks": [mock_callback],
        "tags": ["lyoko", "ns:default", "alert:OOMKilled"],
        "metadata": {"langfuse_session_id": "incident-api-123"},
        "run_name": "lyoko-OOMKilled-api-123",
    }

    app = FastAPI()
    router = create_webhook_router(mock_workflow, tracer=mock_tracer)
    app.include_router(router)
    client = TestClient(app)

    payload = {
        "status": "firing",
        "alerts": [
            {
                "status": "firing",
                "labels": {
                    "alertname": "OOMKilled",
                    "namespace": "default",
                    "pod": "api-123",
                },
            }
        ],
    }

    response = client.post("/webhook/alertmanager", json=payload)
    assert response.status_code == 200
    mock_tracer.get_trace_config.assert_called_once_with(
        session_id="incident-api-123",
        user_id="alert:OOMKilled",
        trace_name="lyoko-OOMKilled-api-123",
        tags=["lyoko", "ns:default", "alert:OOMKilled"],
        metadata={
            "namespace": "default",
            "pod_name": "api-123",
            "alert_name": "OOMKilled",
            "fingerprint": "",
        },
    )
    mock_workflow.ainvoke.assert_called_once()
    _, kwargs = mock_workflow.ainvoke.call_args
    assert "config" in kwargs
    assert kwargs["config"]["callbacks"] == [mock_callback]
    assert kwargs["config"]["metadata"]["langfuse_session_id"] == "incident-api-123"


def test_alertmanager_webhook_accepts_non_kubernetes_alerts(test_client, mock_workflow):
    payload = {
        "status": "firing",
        "alerts": [
            {
                "status": "firing",
                "labels": {
                    "alertname": "UnifiApOffline",
                    "site": "home",
                    "device": "ap-living-room",
                },
                "annotations": {"summary": "Access point is offline"},
                "fingerprint": "ffee0011",
            }
        ],
    }

    response = test_client.post("/webhook/alertmanager", json=payload)

    assert response.status_code == 200
    mock_workflow.ainvoke.assert_called_once()
    state = mock_workflow.ainvoke.call_args.args[0]
    config = mock_workflow.ainvoke.call_args.kwargs["config"]
    assert state["alert_name"] == "UnifiApOffline"
    assert state["labels"]["device"] == "ap-living-room"
    assert state["annotations"] == {"summary": "Access point is offline"}
    assert state["event_id"] == "incident-ffee0011"
    assert config["configurable"] == {"thread_id": "incident-ffee0011"}
    assert not any(tag.startswith("ns:") for tag in config["tags"])


def test_long_incident_keys_are_hashed_to_fit_telegram_callback_data(test_client, mock_workflow):
    long_pod = "p" * 60
    payload = {
        "status": "firing",
        "alerts": [
            {
                "status": "firing",
                "labels": {"alertname": "X", "namespace": "n", "pod": long_pod},
            }
        ],
    }

    test_client.post("/webhook/alertmanager", json=payload)

    incident_id = mock_workflow.ainvoke.call_args.args[0]["event_id"]
    assert long_pod not in incident_id
    assert len(f"approve:{incident_id}.99") <= 64


def test_alertmanager_webhook_groups_batch_into_single_incident(test_client, mock_workflow):
    payload = {
        "status": "firing",
        "alerts": [
            {
                "status": "firing",
                "labels": {
                    "alertname": "KubeNodeNotReady",
                    "namespace": "default",
                    "node": "worker-1",
                },
                "fingerprint": "fp-node",
            },
            {
                "status": "firing",
                "labels": {
                    "alertname": "TargetDown",
                    "namespace": "default",
                    "job": "node-exporter",
                },
                "fingerprint": "fp-target",
            },
            {
                "status": "firing",
                "labels": {
                    "alertname": "KubePodCrashLooping",
                    "namespace": "default",
                    "pod": "api-1",
                },
                "fingerprint": "fp-pod",
            },
        ],
    }

    response = test_client.post("/webhook/alertmanager", json=payload)
    assert response.status_code == 200

    # Only 1 incident dispatched despite 3 alerts
    mock_workflow.ainvoke.assert_called_once()
    state = mock_workflow.ainvoke.call_args.args[0]
    assert state["alert_name"] == "KubeNodeNotReady"
    assert len(state["correlated_alerts"]) == 2
    assert state["correlated_alerts"][0]["alertname"] == "TargetDown"
    assert state["correlated_alerts"][1]["alertname"] == "KubePodCrashLooping"


def test_alertmanager_webhook_trips_circuit_breaker_on_storm(monkeypatch, mock_workflow):
    monkeypatch.setattr("lyoko.config.settings.alert_storm_threshold", 3)
    monkeypatch.setattr("lyoko.config.settings.alert_storm_window_seconds", 60)

    app = FastAPI()
    router = create_webhook_router(mock_workflow)
    app.include_router(router)
    client = TestClient(app)

    payload = {
        "status": "firing",
        "alerts": [
            {
                "status": "firing",
                "labels": {"alertname": f"Alert{i}", "namespace": f"ns{i}"},
                "fingerprint": f"fp{i}",
            }
            for i in range(5)
        ],
    }

    response = client.post("/webhook/alertmanager", json=payload)
    assert response.status_code == 200

    # When storm trips the circuit breaker, automated agent invocations are paused (0 dispatched)
    mock_workflow.ainvoke.assert_not_called()
