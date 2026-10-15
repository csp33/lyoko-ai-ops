"""Use case for coordinating workflow between chat and incident resolution."""

import logging
from typing import Any

from langchain_core.runnables import RunnableConfig
from lyoko.application.chat.use_cases.handle_turn import HandleChatTurnUseCase
from lyoko.application.incidents.use_cases.resolve import ResolveIncidentUseCase
from lyoko.application.skills.matcher import SkillMatcherService
from lyoko.domain.interfaces.approval import ApprovalManagerInterface
from lyoko.domain.interfaces.chat_service import ChatServiceInterface
from lyoko.domain.interfaces.llm import LLMClientInterface
from lyoko.domain.interfaces.mcp import MCPClientInterface
from lyoko.domain.models.state import is_message

logger = logging.getLogger("lyoko.application.workflow.coordinator")


class CoordinateWorkflowUseCase:
    """Coordinates incoming event between chat interaction and incident resolution."""

    def __init__(
        self,
        mcp_client: MCPClientInterface | None,
        llm: LLMClientInterface | None,
        approval_manager: ApprovalManagerInterface | None = None,
        chat_manager: ChatServiceInterface | None = None,
        diagnose_llm: LLMClientInterface | None = None,
        skill_matcher_service: SkillMatcherService | None = None,
        chat_use_case: HandleChatTurnUseCase | None = None,
        incident_use_case: ResolveIncidentUseCase | Any | None = None,
        default_chat_id: str | None = None,
    ) -> None:
        self.chat_use_case = chat_use_case or HandleChatTurnUseCase(
            mcp_client=mcp_client,
            llm=llm,
            approval_manager=approval_manager,
            chat_manager=chat_manager,
        )
        self.incident_use_case = incident_use_case or ResolveIncidentUseCase(
            mcp_client=mcp_client,
            llm=diagnose_llm or llm,
            approval_manager=approval_manager,
            chat_manager=chat_manager,
            skill_matcher_service=skill_matcher_service,
            default_chat_id=default_chat_id or "",
        )

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
