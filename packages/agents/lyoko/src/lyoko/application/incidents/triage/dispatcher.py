"""Dispatcher evaluating registered deterministic triage handlers."""

from typing import Any

TRIAGE_DIAGNOSE = "diagnose"
TRIAGE_HANDLED = "handled"


def choose_triage(state: dict[str, Any]) -> str:
    """Route a handled fast-path incident to notify, otherwise continue to diagnose."""
    return TRIAGE_HANDLED if state.get("triage") == TRIAGE_HANDLED else TRIAGE_DIAGNOSE
