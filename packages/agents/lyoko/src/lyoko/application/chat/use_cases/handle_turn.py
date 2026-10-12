"""Use case for answering operator messages via chat."""

import logging
from typing import Any

from lyoko.application.agents.supervisor import run_supervised, status_callback
from lyoko.application.memory.use_cases.retrieve import RetrieveMemoryLessonsUseCase
from lyoko.application.prompts.chat import CHAT_SYSTEM_PROMPT
from lyoko.application.safety.tool_gate import GateMode, make_gate
from lyoko.application.workflow.routing.recovery import (
    RECOVERY_ACKNOWLEDGEMENT,
    RecoveryNotificationDetector,
)
from lyoko.domain.interfaces.approval import ApprovalManagerInterface
from lyoko.domain.interfaces.chat_service import ChatServiceInterface
from lyoko.domain.interfaces.embeddings import EmbeddingsServiceInterface
from lyoko.domain.interfaces.llm import LLMClientInterface
from lyoko.domain.interfaces.mcp import MCPClientInterface
from lyoko.domain.interfaces.memory import MemoryRepositoryInterface
from lyoko.domain.interfaces.supervisor import SupervisorInterface

logger = logging.getLogger("lyoko.application.chat.use_cases.handle_turn")

_NO_LLM_REPLY = "Received message: '{text}'. (LLM provider not configured)"


class HandleChatTurnUseCase:
    """Orchestrates an interactive chat turn with memory recall and tool gating."""

    def __init__(
        self,
        mcp_client: MCPClientInterface | None,
        llm: LLMClientInterface | None,
        supervisor: SupervisorInterface | None = None,
        approval_manager: ApprovalManagerInterface | None = None,
        chat_manager: ChatServiceInterface | None = None,
        retrieve_memory_use_case: RetrieveMemoryLessonsUseCase | None = None,
        memory_repository: MemoryRepositoryInterface | None = None,
        embeddings_service: EmbeddingsServiceInterface | None = None,
    ) -> None:
        self.mcp_client = mcp_client
        self.llm = llm
        self.supervisor = supervisor
        self.approval_manager = approval_manager
        self.chat_manager = chat_manager
        self.retrieve_memory_use_case = retrieve_memory_use_case or RetrieveMemoryLessonsUseCase(
            memory_repository=memory_repository,
            embeddings_service=embeddings_service,
        )

    async def execute(self, state: dict[str, Any], config: Any = None) -> dict[str, Any]:
        """Answer the operator via supervised tool execution."""
        text = state.get("text", "")
        history_context = state.get("history_context", "")
        if RecoveryNotificationDetector.is_recovery_notification(text):
            # A recovery notification has nothing to investigate; acknowledge it and stop.
            logger.info("Recovery notification acknowledged without investigation.")
            return {"reply": RECOVERY_ACKNOWLEDGEMENT}

        if self.llm is None:
            return {"reply": _NO_LLM_REPLY.format(text=text)}

        lessons_context = await self.retrieve_memory_use_case.execute_for_chat(text)
        prompt_with_memory = f"{text}{lessons_context}{history_context}"

        gate = make_gate(
            state,
            GateMode.APPROVAL,
            approval_manager=self.approval_manager,
            chat_manager=self.chat_manager,
            mcp_client=self.mcp_client,
        )

        on_status = status_callback(config)

        try:
            answer = await run_supervised(
                state,
                gate,
                config,
                mcp_client=self.mcp_client,
                llm=self.llm,
                supervisor=self.supervisor,
                phase="chat",
                prompt=prompt_with_memory,
                system_prompt=None if self.supervisor is not None else CHAT_SYSTEM_PROMPT,
                on_status=on_status,
            )
        except Exception as exc:
            logger.error("Failed to generate LLM response: %s", exc)
            return {"reply": f"⚠️ Error processing your question: {exc}"}
        return {"reply": answer, "actions": [r.to_dict() for r in gate.records]}
