"""Unit tests for LYOKO agent domain models."""

from lyoko.domain.models.incident import (
    CoordinatorNext,
    Incident,
    IncidentState,
    IncidentStatus,
)
from lyoko.domain.models.memory import (
    FeedbackRequest,
    MemoryEntry,
    MemoryQueryResult,
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


def test_lyoko_memory_domain_models():
    entry = MemoryEntry(
        id=10,
        namespace="monitoring",
        service_name="prometheus",
        alert_name="TargetDown",
        incident_pattern="Connection refused",
        operator_feedback="Check DNS before restart",
        action_rule="Verify endpoint",
    )
    assert entry.id == 10
    assert entry.service_name == "prometheus"
    assert entry.operator_feedback == "Check DNS before restart"

    req = FeedbackRequest(
        namespace="monitoring",
        service_name="prometheus",
        incident_pattern="TargetDown Connection refused",
        operator_feedback="Check DNS before restart",
    )
    assert req.service_name == "prometheus"
    assert req.incident_pattern == "TargetDown Connection refused"

    result = MemoryQueryResult(memory=entry, similarity=0.92)
    assert result.similarity == 0.92
    assert result.memory.id == 10


def test_coordinator_next_enums():
    assert CoordinatorNext.REMEDIATE == "remediate"
    assert CoordinatorNext.NOTIFY == "notify"
    assert CoordinatorNext.CHAT_END == "chat_end"
    assert isinstance(CoordinatorNext.CHAT_END, str)
