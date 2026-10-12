"""Detection of external canary notifications posted by monitoring systems."""

import re

_CANARY_PATTERNS = (
    re.compile(r"\balert for\b.*\bcanary\b", re.IGNORECASE),
    re.compile(r"\bcanary\b.*\b(?:has been triggered|healthcheck failed)\b", re.IGNORECASE),
)

CANARY_ACKNOWLEDGEMENT = (
    "⚠️ Noted: External/Cloudflare canary alert triggered (ISP/LaLiga dynamic blocking detected). "
    "No automated action needed."
)


class CanaryNotificationDetector:
    """Detects whether an incoming message is an external canary check."""

    @staticmethod
    def is_canary_notification(text: str) -> bool:
        """Return True when the text announces an external canary check failure or alert."""
        if not text:
            return False
        return any(pattern.search(text) for pattern in _CANARY_PATTERNS)
