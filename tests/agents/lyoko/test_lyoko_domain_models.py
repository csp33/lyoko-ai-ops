"""Unit tests for LYOKO agent domain models."""

from lyoko.domain.models.incident import (
    CoordinatorNext,
    Incident,
    IncidentStatus,
)


def test_lyoko_incident_model():
    incident = Incident(
        alert_name="KubePodCrashLooping",
        namespace="media",
        pod_name="radarr-79dfb8bf7-x82k",
        deployment_name="radarr",
    )
    assert incident.namespace == "media"
    assert incident.pod_name == "radarr-79dfb8bf7-x82k"
    assert incident.deployment_name == "radarr"
    assert incident.correlated_alerts == []

    incident_with_correlated = Incident(
        alert_name="KubeNodeNotReady",
        correlated_alerts=[{"alertname": "TargetDown", "pod": "exporter-1"}],
    )
    assert len(incident_with_correlated.correlated_alerts) == 1


def test_incident_status_badges():
    assert "RESOLVED" in IncidentStatus.RESOLVED.badge
    assert "ESCALATED" in IncidentStatus.ESCALATED.badge
    assert "FAILED" in IncidentStatus.FAILED.badge
    assert "PENDING" in IncidentStatus.PENDING.badge


def test_coordinator_next_enums():
    assert CoordinatorNext.NOTIFY == "notify"
    assert CoordinatorNext.CHAT_END == "chat_end"
    assert isinstance(CoordinatorNext.CHAT_END, str)
