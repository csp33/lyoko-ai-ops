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
