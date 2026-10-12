"""Detection of recovery / resolution notifications posted by monitoring systems."""

import re

_RECOVERY_PATTERNS = (
    re.compile(r"\bhas been resolved\b", re.IGNORECASE),
    re.compile(r"\bhas been cleared\b", re.IGNORECASE),
    re.compile(r"\bhas been recovered\b", re.IGNORECASE),
    re.compile(r"\bis now resolved\b", re.IGNORECASE),
    re.compile(r"\bpassing successfully\b", re.IGNORECASE),
    re.compile(r"\bno active incidents?\b", re.IGNORECASE),
    re.compile(r"\bstatus:\s*resolved\b", re.IGNORECASE),
    re.compile(r"\[resolved\]", re.IGNORECASE),
)

RECOVERY_ACKNOWLEDGEMENT = (
    "✅ Noted: this is a recovery notification, the alert is resolved. No action needed."
)


class RecoveryNotificationDetector:
    """Detects whether an incoming message or alert is a recovery notification."""

    @staticmethod
    def is_recovery_notification(text: str) -> bool:
        """Return True when the text announces that an alert has recovered or is no longer active."""
        if not text:
            return False
        return any(pattern.search(text) for pattern in _RECOVERY_PATTERNS)
