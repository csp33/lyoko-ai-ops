import hashlib
import json
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

_MAX_KEY_LENGTH = 24


def compute_incident_key(incident: "Incident") -> str:
    """Short, stable identifier for an incident.

    The key ends up in Telegram button callback data, which Telegram limits to 64 bytes, so a
    long value (for example a pod name) is replaced by a hash of the alert's labels.
    """
    key = incident.fingerprint or incident.pod_name
    if key and len(key) <= _MAX_KEY_LENGTH:
        return key
    digest_input = json.dumps(incident.labels, sort_keys=True) + (key or "")
    return hashlib.sha1(digest_input.encode(), usedforsecurity=False).hexdigest()[:16]


class IncidentStatus(StrEnum):
    PENDING = "pending"
    DIAGNOSING = "diagnosing"
    REMEDIATING = "remediating"
    VERIFYING = "verifying"
    RESOLVED = "resolved"
    ESCALATED = "escalated"
    FAILED = "failed"


class SpecialistDomain(StrEnum):
    KUBERNETES = "kubernetes"
    UNIFI = "unifi"
    HOMEASSISTANT = "homeassistant"
    GRAFANA = "grafana"
    LANGFUSE = "langfuse"


class CoordinatorNext(StrEnum):
    KUBERNETES = "kubernetes"
    UNIFI = "unifi"
    HOMEASSISTANT = "homeassistant"
    GRAFANA = "grafana"
    LANGFUSE = "langfuse"
    REMEDIATE = "remediate"
    CHAT_END = "chat_end"


@dataclass(frozen=True)
class Incident:
    """An alert LYOKO must investigate.

    Only ``alert_name`` is required: incidents are not tied to Kubernetes. The Kubernetes
    fields are optional conveniences filled in when the alert carries those labels.
    """

    alert_name: str
    namespace: str = ""
    pod_name: str = ""
    deployment_name: str | None = None
    fingerprint: str | None = None
    labels: dict[str, str] = field(default_factory=dict)
    annotations: dict[str, str] = field(default_factory=dict)
    correlated_alerts: list[dict[str, Any]] = field(default_factory=list)

    @property
    def key(self) -> str:
        return compute_incident_key(self)


@dataclass
class IncidentState:
    incident: Incident
    status: IncidentStatus = IncidentStatus.PENDING
    diagnostics: dict[str, Any] = field(default_factory=dict)
    root_cause: str | None = None
    action_taken: str | None = None
    verification_success: bool = False
    error_message: str | None = None
    retry_count: int = 0


@dataclass(frozen=True)
class Diagnosis:
    root_cause: str
    actionable: bool
    plan: str
