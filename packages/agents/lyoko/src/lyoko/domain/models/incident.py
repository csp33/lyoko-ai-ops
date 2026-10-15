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
    RESOLVED = "resolved"
    ESCALATED = "escalated"
    FAILED = "failed"

    @property
    def badge(self) -> str:
        """Formatted Markdown status badge for live messages and reports."""
        badges = {
            IncidentStatus.RESOLVED: "✅ RESOLVED",
            IncidentStatus.ESCALATED: "🚨 ESCALATED / REQUIRES ACTION",
            IncidentStatus.FAILED: "❌ FAILED",
            IncidentStatus.PENDING: "⏳ PENDING",
        }
        return badges.get(self, f"ℹ️ {self.value.upper()}")


class CoordinatorNext(StrEnum):
    NOTIFY = "notify"
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
