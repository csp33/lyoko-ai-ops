"""Use case for coordinating workflow between chat and incident diagnosis."""

import logging
from typing import Any

from langchain_core.runnables import RunnableConfig
from lyoko.application.chat.use_cases.handle_turn import HandleChatTurnUseCase
from lyoko.application.incidents.use_cases.diagnose import DiagnoseIncidentUseCase
from lyoko.application.skills.matcher import SkillMatcherService
from lyoko.domain.interfaces.approval import ApprovalManagerInterface
from lyoko.domain.interfaces.chat_service import ChatServiceInterface
from lyoko.domain.interfaces.embeddings import EmbeddingsServiceInterface
from lyoko.domain.interfaces.llm import LLMClientInterface
from lyoko.domain.interfaces.mcp import MCPClientInterface
from lyoko.domain.interfaces.memory import MemoryRepositoryInterface
from lyoko.domain.interfaces.supervisor import SupervisorInterface
from lyoko.domain.models.state import is_message

logger = logging.getLogger("lyoko.application.workflow.coordinator")


class CoordinateWorkflowUseCase:
    """Coordinates incoming event between chat interaction and incident diagnosis."""

    def __init__(
        self,
        mcp_client: MCPClientInterface | None,
        llm: LLMClientInterface | None,
        supervisor: SupervisorInterface | None = None,
        approval_manager: ApprovalManagerInterface | None = None,
        chat_manager: ChatServiceInterface | None = None,
        memory_repository: MemoryRepositoryInterface | None = None,
        embeddings_service: EmbeddingsServiceInterface | None = None,
        diagnose_llm: LLMClientInterface | None = None,
        diagnose_supervisor: SupervisorInterface | None = None,
        skill_matcher_service: SkillMatcherService | None = None,
        chat_use_case: HandleChatTurnUseCase | None = None,
        diagnose_use_case: DiagnoseIncidentUseCase | None = None,
    ) -> None:
        self.chat_use_case = chat_use_case or HandleChatTurnUseCase(
            mcp_client=mcp_client,
            llm=llm,
            supervisor=supervisor,
            approval_manager=approval_manager,
            chat_manager=chat_manager,
            memory_repository=memory_repository,
            embeddings_service=embeddings_service,
        )
        self.diagnose_use_case = diagnose_use_case or DiagnoseIncidentUseCase(
            mcp_client=mcp_client,
            llm=diagnose_llm or llm,
            supervisor=diagnose_supervisor or supervisor,
            approval_manager=approval_manager,
            chat_manager=chat_manager,
            memory_repository=memory_repository,
            embeddings_service=embeddings_service,
            skill_matcher_service=skill_matcher_service,
        )

    async def execute(
        self, state: dict[str, Any], config: RunnableConfig | None = None
    ) -> dict[str, Any]:
        """Execute coordinator turn for either chat or incident investigation."""
        logger.info(
            "Executing coordinator use case for event_type='%s', route='%s'",
            state.get("event_type"),
            state.get("route"),
        )
        if is_message(state) and state.get("route") != "incident":
            return await self.chat_use_case.execute(state, config=config)
        return await self.diagnose_use_case.execute(state, config=config)
