"""Formatting utilities for incident alerts, context, and approval origins."""

from typing import Any

from lyoko.domain.models.state import is_message

_MAX_SUBJECT_CHARS = 200


class IncidentContextFormatter:
    """Formats incident labels, annotations, history, and approval origins."""

    @staticmethod
    def truncate(text: str, max_chars: int = _MAX_SUBJECT_CHARS) -> str:
        text = " ".join(text.split())
        return text if len(text) <= max_chars else text[:max_chars] + "..."

    @staticmethod
    def format_pairs(pairs: dict[str, str]) -> str:
        return "\n".join(f"- {key}: {value}" for key, value in sorted(pairs.items())) or "- (none)"

    @staticmethod
    def format_correlated(alerts: list[dict[str, Any]] | None) -> str:
        if not alerts:
            return ""
        lines = [f"\nCorrelated / Cascade Alerts ({len(alerts)}):"]
        for alert in alerts:
            name = alert.get("alertname") or alert.get("labels", {}).get(
                "alertname", "UnknownAlert"
            )
            ns = alert.get("namespace") or alert.get("labels", {}).get("namespace", "")
            pod = alert.get("pod") or alert.get("labels", {}).get("pod", "")
            details = []
            if ns:
                details.append(f"namespace: {ns}")
            if pod:
                details.append(f"pod: {pod}")
            detail_str = f" ({', '.join(details)})" if details else ""
            lines.append(f"- {name}{detail_str}")
        return "\n".join(lines)

    @classmethod
    def format_context(cls, state: dict[str, Any]) -> str:
        if is_message(state):
            return f"Problem reported by the operator in chat:\n{state.get('text', '')}"
        base = (
            f"Alert: {state.get('alert_name', 'UnknownAlert')}\n"
            f"Labels:\n{cls.format_pairs(state.get('labels') or {})}\n"
            f"Annotations:\n{cls.format_pairs(state.get('annotations') or {})}"
        )
        correlated = cls.format_correlated(state.get("correlated_alerts"))
        return f"{base}\n{correlated}".rstrip()

    @classmethod
    def format_origin(cls, state: dict[str, Any]) -> str:
        """One line telling the operator what triggered the run (shown in approval requests)."""
        if is_message(state):
            return f"Chat request: {cls.truncate(state.get('text', ''))}"
        return f"Alert: {state.get('alert_name', 'UnknownAlert')}"

    @staticmethod
    def describe_call(record: dict[str, Any]) -> str:
        return f"`{record['tool']}` ({record['outcome'].replace('_', ' ')})"
