"""Use case for fast-path deterministic incident triage."""

import logging
from collections.abc import Sequence
from typing import Any

from langchain_core.runnables import RunnableConfig
from lyoko.application.incidents.triage.base import TriageHandler
from lyoko.application.incidents.triage.dispatcher import TRIAGE_DIAGNOSE

logger = logging.getLogger("lyoko.application.incidents.use_cases.triage")


class TriageIncidentUseCase:
    """Evaluates registered deterministic triage handlers before full multi-agent diagnosis."""

    def __init__(self, handlers: Sequence[TriageHandler] | None = None) -> None:
        self.handlers = list(handlers) if handlers is not None else []

    async def execute(
        self, state: dict[str, Any], config: RunnableConfig | None = None
    ) -> dict[str, Any]:
        """Iterate through handlers and return early if any handler resolves the incident."""
        for handler in self.handlers:
            try:
                if handler.can_handle(state):
                    result = await handler.execute(state, config)
                    if result is not None and result.handled:
                        return result.to_state_patch()
            except Exception as exc:
                logger.warning(
                    "Triage handler %s failed: %s; falling through.",
                    handler.__class__.__name__,
                    exc,
                )
        return {"triage": TRIAGE_DIAGNOSE}
