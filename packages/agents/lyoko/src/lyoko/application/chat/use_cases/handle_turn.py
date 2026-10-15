"""Use case for answering operator messages via chat."""

import logging
from typing import Any

from lyoko.application.agents.runner import AgentRunner, run_agent, status_callback
from lyoko.application.prompts.chat import CHAT_SYSTEM_PROMPT
from lyoko.application.safety.tool_gate import GateMode, make_gate
from lyoko.application.workflow.routing.canary import (
    CANARY_ACKNOWLEDGEMENT,
    CanaryNotificationDetector,
)
from lyoko.application.workflow.routing.recovery import (
    RECOVERY_ACKNOWLEDGEMENT,
    RecoveryNotificationDetector,
)
from lyoko.domain.interfaces.approval import ApprovalManagerInterface
from lyoko.domain.interfaces.chat_service import ChatServiceInterface
from lyoko.domain.interfaces.llm import LLMClientInterface
from lyoko.domain.interfaces.mcp import MCPClientInterface

logger = logging.getLogger("lyoko.application.chat.use_cases.handle_turn")

_NO_LLM_REPLY = "Received message: '{text}'. (LLM provider not configured)"


class HandleChatTurnUseCase:
    """Orchestrates an interactive chat turn with tool gating."""

    def __init__(
        self,
        mcp_client: MCPClientInterface | None,
        llm: LLMClientInterface | None,
        approval_manager: ApprovalManagerInterface | None = None,
        chat_manager: ChatServiceInterface | None = None,
        runner: Any = None,
    ) -> None:
        self.mcp_client = mcp_client
        self.llm = llm
        self.approval_manager = approval_manager
        self.chat_manager = chat_manager
        self.runner = runner or run_agent

    async def execute(self, state: dict[str, Any], config: Any = None) -> dict[str, Any]:
        """Answer the operator via Hermes tool execution."""
        text = state.get("text", "")
        if RecoveryNotificationDetector.is_recovery_notification(text):
            logger.info("Recovery notification acknowledged without investigation.")
            return {"reply": RECOVERY_ACKNOWLEDGEMENT}

        if CanaryNotificationDetector.is_canary_notification(text):
            logger.info("Canary notification acknowledged without investigation.")
            return {"reply": CANARY_ACKNOWLEDGEMENT}

        if self.llm is None:
            return {"reply": _NO_LLM_REPLY.format(text=text)}

        gate = make_gate(
            state,
            GateMode.APPROVAL,
            approval_manager=self.approval_manager,
            chat_manager=self.chat_manager,
            mcp_client=self.mcp_client,
        )

        on_status = status_callback(config)

        try:
            if isinstance(self.runner, AgentRunner):
                answer = await self.runner.run(
                    state,
                    gate,
                    config,
                    mcp_client=self.mcp_client,
                    llm=self.llm,
                    phase="chat",
                    prompt=text,
                    system_prompt=CHAT_SYSTEM_PROMPT,
                    on_status=on_status,
                )
            else:
                answer = await self.runner(
                    state,
                    gate,
                    config,
                    mcp_client=self.mcp_client,
                    llm=self.llm,
                    phase="chat",
                    prompt=text,
                    system_prompt=CHAT_SYSTEM_PROMPT,
                    on_status=on_status,
                )
        except Exception as exc:
            logger.error("Failed to generate LLM response: %s", exc)
            return {"reply": f"⚠️ Error processing your question: {exc}"}
        return {"reply": answer, "actions": [r.to_dict() for r in gate.records]}
