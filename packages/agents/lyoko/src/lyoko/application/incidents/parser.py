"""Answer parsers for incident agent phases."""

import re

_AUTONOMOUS_ROOT_CAUSE_RE = re.compile(
    r"^ROOT_CAUSE:\s*(.*?)(?=^ACTION_TAKEN:|^ACTIONABLE:|\Z)", re.S | re.M | re.I
)
_ACTION_TAKEN_RE = re.compile(
    r"^ACTION_TAKEN:\s*(.*?)(?=^VERIFICATION:|^STATUS:|\Z)", re.S | re.M | re.I
)
_VERIFICATION_RE = re.compile(r"^VERIFICATION:\s*(.*?)(?=^STATUS:|\Z)", re.S | re.M | re.I)
_STATUS_RE = re.compile(r"^STATUS:\s*(RESOLVED|ESCALATED)\b", re.M | re.I)
_ACTIONABLE_RE = re.compile(r"^ACTIONABLE:\s*(yes|no)\b", re.M | re.I)


class IncidentOutputParser:
    """Parses output from incident investigation and resolution runs."""

    @staticmethod
    def parse_resolution(text: str) -> dict[str, str | bool]:
        """Parse an autonomous end-to-end incident resolution report."""
        root_cause_m = _AUTONOMOUS_ROOT_CAUSE_RE.search(text)
        action_m = _ACTION_TAKEN_RE.search(text)
        verif_m = _VERIFICATION_RE.search(text)
        status_m = _STATUS_RE.search(text)
        actionable_m = _ACTIONABLE_RE.search(text)

        root_cause = (
            root_cause_m.group(1).strip() if root_cause_m else ""
        ) or "Investigated with tools."
        action_taken = action_m.group(1).strip() if action_m else ""
        verification = verif_m.group(1).strip() if verif_m else ""

        if status_m:
            is_resolved = status_m.group(1).upper() == "RESOLVED"
        elif actionable_m:
            is_resolved = actionable_m.group(1).lower() == "yes"
        else:
            is_resolved = False

        if not action_taken and not verification:
            action_taken = text.strip()[:400]

        return {
            "root_cause": root_cause,
            "action_taken": action_taken,
            "verification": verification,
            "is_resolved": is_resolved,
            "requires_escalation": not is_resolved,
        }
