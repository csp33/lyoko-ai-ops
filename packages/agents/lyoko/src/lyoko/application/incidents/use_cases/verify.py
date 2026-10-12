"""Use case for verifying incident resolution after remediation."""

import asyncio
import logging
from typing import Any

from langchain_core.runnables import RunnableConfig
from lyoko.application.agents.supervisor import run_supervised, status_callback
from lyoko.application.context.formatter import IncidentContextFormatter
from lyoko.application.incidents.parser import IncidentOutputParser
from lyoko.application.prompts.incident import VERIFY_SYSTEM_PROMPT
from lyoko.application.safety.tool_gate import CallOutcome, GateMode, make_gate
from lyoko.config import settings
from lyoko.domain.interfaces.approval import ApprovalManagerInterface
from lyoko.domain.interfaces.chat_service import ChatServiceInterface
from lyoko.domain.interfaces.llm import LLMClientInterface
from lyoko.domain.interfaces.mcp import MCPClientInterface
from lyoko.domain.interfaces.supervisor import SupervisorInterface

logger = logging.getLogger("lyoko.application.incidents.use_cases.verify")

_EXECUTED_OUTCOMES = frozenset({CallOutcome.AUTO_APPROVED.value, CallOutcome.APPROVED.value})


class VerifyIncidentUseCase:
    """Verifies in read-only mode that an incident is resolved after remediation."""

    def __init__(
        self,
        mcp_client: MCPClientInterface | None,
        llm: LLMClientInterface | None,
        supervisor: SupervisorInterface | None = None,
        approval_manager: ApprovalManagerInterface | None = None,
        chat_manager: ChatServiceInterface | None = None,
        delay_seconds: float | None = None,
    ) -> None:
        self.mcp_client = mcp_client
        self.llm = llm
        self.supervisor = supervisor
        self.approval_manager = approval_manager
        self.chat_manager = chat_manager
        self.delay_seconds = (
            delay_seconds
            if delay_seconds is not None
            else getattr(settings, "verification_delay_seconds", 10)
        )

    async def execute(
        self, state: dict[str, Any], config: RunnableConfig | None = None
    ) -> dict[str, Any]:
        """Check, read-only, that the incident is actually resolved."""
        executed = [a for a in state.get("actions", []) if a["outcome"] in _EXECUTED_OUTCOMES]
        if state.get("requires_escalation") or not executed or self.llm is None:
            return {"is_resolved": False}

        logger.info("Waiting %ss for stabilization...", self.delay_seconds)
        await asyncio.sleep(self.delay_seconds)

        gate = make_gate(
            state,
            GateMode.READ_ONLY,
            approval_manager=self.approval_manager,
            chat_manager=self.chat_manager,
            mcp_client=self.mcp_client,
        )
        changes = "\n".join(
            f"- {IncidentContextFormatter.describe_call(a)} {a['arguments']}" for a in executed
        )
        try:
            answer = await run_supervised(
                state,
                gate,
                config,
                mcp_client=self.mcp_client,
                llm=self.llm,
                supervisor=self.supervisor,
                phase="verify",
                system_prompt=VERIFY_SYSTEM_PROMPT,
                prompt=(
                    f"Verify this incident is resolved.\n\n{IncidentContextFormatter.format_context(state)}\n\n"
                    f"Root cause: {state.get('root_cause', 'Unknown')}\n\n"
                    f"Changes applied:\n{changes}"
                ),
                on_status=status_callback(config),
            )
        except Exception as exc:
            logger.error("Verification failed: %s", exc, exc_info=True)
            return {"is_resolved": False, "verification": f"Verification failed: {exc}"}

        resolved, evidence = IncidentOutputParser.parse_verdict(answer)
        return {"is_resolved": resolved, "verification": evidence}
