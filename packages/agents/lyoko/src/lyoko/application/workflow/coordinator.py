"""Use case for coordinating workflow between chat and incident resolution."""

import logging
from typing import Any

from langchain_core.runnables import RunnableConfig
from lyoko.application.chat.use_cases.handle_turn import HandleChatTurnUseCase
from lyoko.application.incidents.use_cases.resolve import ResolveIncidentUseCase
from lyoko.domain.models.state import is_message

logger = logging.getLogger("lyoko.application.workflow.coordinator")


class CoordinateWorkflowUseCase:
    """Coordinates incoming event between chat interaction and incident resolution."""

    def __init__(
        self,
        chat_use_case: HandleChatTurnUseCase,
        incident_use_case: ResolveIncidentUseCase,
    ) -> None:
        self.chat_use_case = chat_use_case
        self.incident_use_case = incident_use_case

    async def execute(
        self, state: dict[str, Any], config: RunnableConfig | None = None
    ) -> dict[str, Any]:
        """Execute coordinator turn for either chat or incident resolution."""
        logger.info(
            "Executing coordinator use case for event_type='%s', route='%s'",
            state.get("event_type"),
            state.get("route"),
        )
        if is_message(state) and state.get("route") != "incident":
            return await self.chat_use_case.execute(state, config=config)
        return await self.incident_use_case.execute(state, config=config)
