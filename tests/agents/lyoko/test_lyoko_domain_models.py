"""Unit tests for LYOKO agent domain models."""

from lyoko.domain.models.incident import (
    CoordinatorNext,
    Incident,
    IncidentState,
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


def test_lyoko_incident_state():
    incident = Incident(
        alert_name="KubePodCrashLooping",
        namespace="media",
        pod_name="radarr-79dfb8bf7-x82k",
        deployment_name="radarr",
    )
    state = IncidentState(
        incident=incident,
        status=IncidentStatus.DIAGNOSING,
        diagnostics={"logs": "Out of memory"},
        action_taken="Bumped limit to 1Gi",
        verification_success=True,
    )
    assert state.incident.deployment_name == "radarr"
    assert state.status == IncidentStatus.DIAGNOSING
    assert state.diagnostics["logs"] == "Out of memory"
    assert state.verification_success is True


def test_coordinator_next_enums():
    assert CoordinatorNext.REMEDIATE == "remediate"
    assert CoordinatorNext.NOTIFY == "notify"
    assert CoordinatorNext.CHAT_END == "chat_end"
    assert isinstance(CoordinatorNext.CHAT_END, str)
