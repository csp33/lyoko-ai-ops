"""Use case for autonomous incident resolution with approval-gated tool execution."""

import logging
from typing import Any

from lyoko.application.agents.supervisor import run_supervised, status_callback
from lyoko.application.context.formatter import IncidentContextFormatter
from lyoko.application.incidents.parser import IncidentOutputParser
from lyoko.application.incidents.status import IncidentStatusFormatter
from lyoko.application.memory.use_cases.retrieve import RetrieveMemoryLessonsUseCase
from lyoko.application.prompts.incident import RESOLVE_SYSTEM_PROMPT
from lyoko.application.safety.tool_gate import CallOutcome, GateMode, make_gate
from lyoko.application.skills.matcher import SkillMatcherService
from lyoko.domain.interfaces.approval import ApprovalManagerInterface
from lyoko.domain.interfaces.chat_service import ChatServiceInterface
from lyoko.domain.interfaces.embeddings import EmbeddingsServiceInterface
from lyoko.domain.interfaces.llm import LLMClientInterface
from lyoko.domain.interfaces.mcp import MCPClientInterface
from lyoko.domain.interfaces.memory import MemoryRepositoryInterface
from lyoko.domain.interfaces.supervisor import SupervisorInterface
from lyoko.domain.models.incident import CoordinatorNext
from lyoko.domain.models.state import is_message

logger = logging.getLogger("lyoko.application.incidents.use_cases.resolve")


class ResolveIncidentUseCase:
    """Resolves an incident end-to-end through autonomous, approval-gated tool execution."""

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
        skill_matcher_service: SkillMatcherService | None = None,
        runner: Any = None,
        default_chat_id: str | None = None,
    ) -> None:
        self.mcp_client = mcp_client
        self.llm = llm
        self.supervisor = supervisor
        self.approval_manager = approval_manager
        self.chat_manager = chat_manager
        self.skill_matcher_service = skill_matcher_service
        self.runner = runner or run_supervised
        if retrieve_memory_use_case is not None:
            self.retrieve_memory_use_case = retrieve_memory_use_case
        elif memory_repository is not None:
            self.retrieve_memory_use_case = RetrieveMemoryLessonsUseCase(
                memory_repository=memory_repository,
                embeddings_service=embeddings_service,
            )
        else:
            self.retrieve_memory_use_case = None
        self.default_chat_id = default_chat_id or ""

    async def execute(self, state: dict[str, Any], config: Any = None) -> dict[str, Any]:
        """Investigate, formulate plan, execute changes under approval gate, and verify."""
        if self.llm is None:
            return {
                "root_cause": "Not investigated: no LLM provider is configured.",
                "action_taken": "No action taken.",
                "verification": "",
                "is_resolved": False,
                "requires_escalation": True,
                "coordinator_next": CoordinatorNext.NOTIFY,
            }

        if self.retrieve_memory_use_case is not None:
            (
                matched_memories,
                lessons_context,
            ) = await self.retrieve_memory_use_case.execute_for_incident(state=state)
        else:
            matched_memories, lessons_context = [], ""

        logger.info("Resolving %s autonomously...", IncidentContextFormatter.format_origin(state))
        progress_msg_id: str | None = state.get("progress_message_id")
        progress_chat_id: str | None = state.get("progress_chat_id")

        if self.chat_manager is not None and not is_message(state) and not progress_msg_id:
            chat_id = state.get("chat_id") or self.default_chat_id
            if chat_id:
                status_text = IncidentStatusFormatter.format_diagnosing_status(state)
                sent_list = await self.chat_manager.broadcast_message(
                    chat_id=chat_id,
                    text=status_text,
                    session_id=state.get("session_id"),
                )
                if sent_list:
                    progress_msg_id = sent_list[0].message_id
                    progress_chat_id = sent_list[0].chat_id

        gate = make_gate(
            state,
            GateMode.APPROVAL,
            approval_manager=self.approval_manager,
            chat_manager=self.chat_manager,
            mcp_client=self.mcp_client,
        )
        try:
            skills_context = ""
            if self.skill_matcher_service is not None:
                skills_context = await self.skill_matcher_service.format_matched_skills_context(
                    alert_name=state.get("alert_name", ""),
                    labels=state.get("labels"),
                    annotations=state.get("annotations"),
                )

            prompt_content = (
                f"Resolve this incident.\n\n"
                f"{IncidentContextFormatter.format_context(state)}"
                f"{lessons_context}{skills_context}"
            )

            answer = await self.runner(
                state,
                gate,
                config,
                mcp_client=self.mcp_client,
                llm=self.llm,
                supervisor=self.supervisor,
                phase="incident",
                system_prompt=RESOLVE_SYSTEM_PROMPT,
                prompt=prompt_content,
                on_status=status_callback(config),
            )
        except Exception as exc:
            logger.error("Incident resolution failed: %s", exc, exc_info=True)
            return {
                "root_cause": f"Resolution failed: {exc}",
                "action_taken": "Aborted due to error.",
                "verification": "",
                "is_resolved": False,
                "requires_escalation": True,
                "coordinator_next": CoordinatorNext.NOTIFY,
            }

        resolution = IncidentOutputParser.parse_resolution(answer)
        records = gate.records
        actions = [r.to_dict() for r in records]
        denied = [r for r in records if r.outcome is CallOutcome.DENIED]
        refused = [r for r in records if r.outcome is CallOutcome.REFUSED]

        if denied:
            resolution["action_taken"] = f"Remediation aborted: {denied[0].reason}"
            resolution["requires_escalation"] = True
            resolution["is_resolved"] = False
        elif refused:
            resolution["action_taken"] = f"Remediation aborted: {refused[0].reason}"
            resolution["requires_escalation"] = True
            resolution["is_resolved"] = False
        elif not resolution["action_taken"]:
            resolution["action_taken"] = "Investigated. No modifications required."

        return {
            "root_cause": resolution["root_cause"],
            "action_taken": resolution["action_taken"],
            "actions": actions,
            "verification": resolution["verification"],
            "is_resolved": resolution["is_resolved"],
            "requires_escalation": resolution["requires_escalation"],
            "matched_memories": matched_memories,
            "lessons_context": lessons_context,
            "progress_message_id": progress_msg_id,
            "progress_chat_id": progress_chat_id,
            "coordinator_next": CoordinatorNext.NOTIFY,
        }
