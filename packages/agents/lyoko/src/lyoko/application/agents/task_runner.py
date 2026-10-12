"""Use case for delegating a scoped investigation or task to a domain specialist."""

import logging
from typing import Any

from langchain_core.runnables import RunnableConfig
from lyoko.application.agents.supervisor import status_callback
from lyoko.application.safety.tool_gate import GateMode, make_gate
from lyoko.domain.interfaces.approval import ApprovalManagerInterface
from lyoko.domain.interfaces.chat_service import ChatServiceInterface
from lyoko.domain.interfaces.mcp import MCPClientInterface
from lyoko.domain.interfaces.specialist import SpecialistAgentInterface

logger = logging.getLogger("lyoko.application.agents.task_runner")


class ExecuteSpecialistTaskUseCase:
    """Carries out a delegated task using a domain specialist agent under a tool gate."""

    def __init__(
        self,
        domain: str,
        specialist: SpecialistAgentInterface,
        mcp_client: MCPClientInterface | None = None,
        approval_manager: ApprovalManagerInterface | None = None,
        chat_manager: ChatServiceInterface | None = None,
    ) -> None:
        self.domain = domain
        self.specialist = specialist
        self.mcp_client = mcp_client
        self.approval_manager = approval_manager
        self.chat_manager = chat_manager

    async def execute(
        self, state: dict[str, Any], config: RunnableConfig | None = None
    ) -> dict[str, Any]:
        """Execute specialist task if there is a pending delegation for this domain."""
        pending = state.get("pending_delegation")
        if not pending or pending.get("domain") != self.domain:
            logger.warning(
                "Specialist '%s' called without matching pending delegation (%s)",
                self.domain,
                pending,
            )
            return {"pending_delegation": None}

        task = pending.get("task", "")
        mode_str = pending.get("gate_mode", GateMode.READ_ONLY.value)
        gate_mode = GateMode.APPROVAL if mode_str == GateMode.APPROVAL.value else GateMode.READ_ONLY

        gate = make_gate(
            state,
            gate_mode,
            approval_manager=self.approval_manager,
            chat_manager=self.chat_manager,
            mcp_client=self.mcp_client,
        )

        on_status = status_callback(config)
        session_id = state.get("session_id")

        logger.info("Executing specialist for domain '%s' with task: %s", self.domain, task)
        try:
            response = await self.specialist.run(
                prompt=task,
                session_id=session_id,
                authorizer=gate.authorize,
                parent_config=config,
                on_status=on_status,
            )
        except Exception as exc:
            logger.error("Error executing specialist '%s': %s", self.domain, exc)
            response = f"Specialist error ({self.domain}): {exc}"

        history = list(state.get("delegation_history") or [])
        history.append(
            {
                "domain": self.domain,
                "task": task,
                "response": response,
            }
        )

        return {
            "pending_delegation": None,
            "delegation_history": history,
            "actions": list(state.get("actions") or []) + [r.to_dict() for r in gate.records],
        }
