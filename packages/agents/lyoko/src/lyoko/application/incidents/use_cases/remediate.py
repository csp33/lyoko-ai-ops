"""Use case for remediating incidents with approval-gated tool execution."""

import logging
from typing import Any

from langchain_core.runnables import RunnableConfig
from lyoko.application.agents.supervisor import run_supervised, status_callback
from lyoko.application.context.formatter import IncidentContextFormatter
from lyoko.application.incidents.parser import IncidentOutputParser
from lyoko.application.incidents.status import IncidentStatusFormatter
from lyoko.application.prompts.incident import REMEDIATE_SYSTEM_PROMPT
from lyoko.application.safety.tool_gate import CallOutcome, GateMode, ToolCallRecord, make_gate
from lyoko.domain.interfaces.approval import ApprovalManagerInterface
from lyoko.domain.interfaces.chat_service import ChatServiceInterface
from lyoko.domain.interfaces.llm import LLMClientInterface
from lyoko.domain.interfaces.mcp import MCPClientInterface
from lyoko.domain.interfaces.supervisor import SupervisorInterface

logger = logging.getLogger("lyoko.application.incidents.use_cases.remediate")

_NO_FIX_MESSAGE = "No automated fix applied (requires human inspection)"


def _remediation_outcome(
    records: list[ToolCallRecord], summary: str, error: str | None
) -> dict[str, Any]:
    """Turn the gate's audit trail into the remediation node's state update."""
    actions = [r.to_dict() for r in records]
    denied = [r for r in records if r.outcome is CallOutcome.DENIED]
    refused = [r for r in records if r.outcome is CallOutcome.REFUSED]
    executed = [r for r in records if r.executed]

    if denied:
        action_taken = f"Remediation aborted: {denied[0].reason}"
    elif refused:
        action_taken = f"Remediation aborted: {refused[0].reason}"
    elif error is not None:
        action_taken = f"Remediation failed: {error}"
    elif not executed:
        action_taken = f"{_NO_FIX_MESSAGE}. {summary}".strip()
    else:
        action_taken = summary or "Changes applied."

    escalate = bool(denied or refused or error is not None or not executed)
    return {"action_taken": action_taken, "actions": actions, "requires_escalation": escalate}


class RemediateIncidentUseCase:
    """Executes the remediation plan formulated during diagnosis."""

    def __init__(
        self,
        mcp_client: MCPClientInterface | None,
        llm: LLMClientInterface | None,
        supervisor: SupervisorInterface | None = None,
        approval_manager: ApprovalManagerInterface | None = None,
        chat_manager: ChatServiceInterface | None = None,
    ) -> None:
        self.mcp_client = mcp_client
        self.llm = llm
        self.supervisor = supervisor
        self.approval_manager = approval_manager
        self.chat_manager = chat_manager

    async def execute(
        self, state: dict[str, Any], config: RunnableConfig | None = None
    ) -> dict[str, Any]:
        """Carry out the plan. State-changing tools wait for operator approval."""
        if state.get("requires_escalation") or self.llm is None:
            return {"action_taken": _NO_FIX_MESSAGE, "requires_escalation": True}

        gate = make_gate(
            state,
            GateMode.APPROVAL,
            approval_manager=self.approval_manager,
            chat_manager=self.chat_manager,
            plan=state.get("plan", ""),
            mcp_client=self.mcp_client,
        )
        error: str | None = None
        summary = ""
        try:
            answer = await run_supervised(
                state,
                gate,
                config,
                mcp_client=self.mcp_client,
                llm=self.llm,
                supervisor=self.supervisor,
                phase="remediate",
                system_prompt=REMEDIATE_SYSTEM_PROMPT,
                prompt=(
                    f"Carry out this remediation plan.\n\n{IncidentContextFormatter.format_context(state)}\n\n"
                    f"Root cause: {state.get('root_cause', 'Unknown')}\n\n"
                    f"Plan:\n{state.get('plan', '')}"
                ),
                on_status=status_callback(config),
            )
            summary = IncidentOutputParser.parse_result(answer)
        except Exception as exc:
            logger.error("Remediation failed: %s", exc, exc_info=True)
            error = str(exc)

        outcome = _remediation_outcome(gate.records, summary, error)
        progress_msg_id = state.get("progress_message_id")
        progress_chat_id = state.get("progress_chat_id")
        if self.chat_manager is not None and progress_msg_id and progress_chat_id:
            if not outcome.get("requires_escalation"):
                status_text = IncidentStatusFormatter.format_verifying_status(
                    state,
                    state.get("root_cause", "Unknown"),
                    outcome.get("action_taken", "Changes applied."),
                )
            else:
                status_text = IncidentStatusFormatter.format_remediating_status(
                    state,
                    state.get("root_cause", "Unknown"),
                    requires_escalation=True,
                )
            await self.chat_manager.edit_message(
                chat_id=progress_chat_id,
                message_id=progress_msg_id,
                text=status_text,
            )
        return outcome
