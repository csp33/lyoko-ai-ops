"""Formatting helpers for real-time incident status progress updates."""

from typing import Any

from lyoko.application.context.formatter import IncidentContextFormatter
from lyoko.domain.models.state import is_message


class IncidentStatusFormatter:
    """Formats real-time incident status progress updates for Telegram/Chat."""

    @staticmethod
    def format_diagnosing_status(state: dict[str, Any]) -> str:
        """Build a live progress update when incident diagnosis starts."""
        lines = ["⏳ *[LYOKO Incident in Progress]*", ""]
        if is_message(state):
            lines.append(
                f"🔍 *Phase: Diagnose* — _Investigating:_ {IncidentContextFormatter.truncate(state.get('text', ''))}"
            )
        else:
            alert_name = state.get("alert_name") or "Unknown Alert"
            labels = state.get("labels") or {}
            target = ", ".join(f"{k}={labels[k]}" for k in sorted(labels) if k != "alertname")
            lines.append(f"🔍 *Phase: Diagnose* — _Investigating alert:_ `{alert_name}`")
            if target:
                lines.append(f"• *Target:* `{target}`")
        return "\n".join(lines)

    @staticmethod
    def format_remediating_status(
        state: dict[str, Any],
        root_cause: str,
        requires_escalation: bool = False,
    ) -> str:
        """Build a live progress update after diagnosis completes."""
        lines = ["⏳ *[LYOKO Incident in Progress]*", ""]
        if is_message(state):
            lines.append(f"• *Report:* {IncidentContextFormatter.truncate(state.get('text', ''))}")
        else:
            alert_name = state.get("alert_name") or "Incident"
            lines.append(f"• *Alert:* `{alert_name}`")

        lines.append(f"• *Root Cause:* {root_cause}")

        if requires_escalation:
            lines.extend(["", "⚠️ *Status:* _Escalation required. Finalizing report..._"])
        else:
            lines.extend(["", "🛠️ *Phase: Remediate* — _Executing remediation plan..._"])
        return "\n".join(lines)

    @staticmethod
    def format_verifying_status(
        state: dict[str, Any],
        root_cause: str,
        action_taken: str,
    ) -> str:
        """Build a live progress update after remediation completes while stabilizing."""
        lines = ["⏳ *[LYOKO Incident in Progress]*", ""]
        if is_message(state):
            lines.append(f"• *Report:* {IncidentContextFormatter.truncate(state.get('text', ''))}")
        else:
            alert_name = state.get("alert_name") or "Incident"
            lines.append(f"• *Alert:* `{alert_name}`")

        lines.append(f"• *Root Cause:* {root_cause}")
        lines.append(f"• *Action Taken:* {action_taken}")
        lines.extend(["", "🔬 *Phase: Verify* — _Verifying cluster stabilization..._"])
        return "\n".join(lines)
