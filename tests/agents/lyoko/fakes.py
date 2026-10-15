"""Test doubles for the incident agent: a scripted LLM and a gateway client that expose the gate."""

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any

from lyoko.application.agents.runner import AgentRunner
from lyoko.application.chat.use_cases.handle_turn import HandleChatTurnUseCase
from lyoko.application.incidents.triage.base import TriageHandler
from lyoko.application.incidents.use_cases.enrich_trace_cost import EnrichTraceCostUseCase
from lyoko.application.incidents.use_cases.notify import NotifyIncidentReportUseCase
from lyoko.application.incidents.use_cases.resolve import ResolveIncidentUseCase
from lyoko.application.incidents.use_cases.triage import TriageIncidentUseCase
from lyoko.application.safety.hitl.manager import ApprovalManager
from lyoko.application.skills.matcher import SkillMatcherService
from lyoko.application.workflow.coordinator import CoordinateWorkflowUseCase
from lyoko.application.workflow.graph import LyokoWorkflow
from lyoko.application.workflow.routing.route_event import RouteEventUseCase
from lyoko.domain.interfaces.llm import LLMClientInterface
from lyoko.domain.interfaces.mcp import MCPClientInterface, ToolAuthorizer
from lyoko.domain.models.chat import ApprovalResponse
from lyoko.infrastructure.chat.manager import ChatManager

RESOLUTION_RESOLVED = (
    "ROOT_CAUSE: The radarr pod was OOMKilled because its memory limit is too low.\n"
    "ACTION_TAKEN: Scaled radarr.\n"
    "VERIFICATION: The pod is Running with 0 restarts.\n"
    "STATUS: RESOLVED"
)
RESOLUTION_ESCALATED = (
    "ROOT_CAUSE: The database credentials are invalid.\n"
    "ACTION_TAKEN: Investigated. No modifications required.\n"
    "VERIFICATION: None\n"
    "STATUS: ESCALATED"
)


@dataclass
class GateHandle:
    """Stands in for a LangChain tool list: carries the authorizer the agent must call."""

    authorizer: ToolAuthorizer | None


class FakeMCPClient:
    """Gateway client double that hands the gate's authorizer to the scripted LLM."""

    def get_langchain_tools(self, authorizer: ToolAuthorizer | None = None) -> list[Any]:
        return [GateHandle(authorizer)]


Behavior = Callable[[ToolAuthorizer], Awaitable[str]]


class ScriptedLLM(LLMClientInterface):
    """LLM double with one scripted behavior per phase (route, chat, incident).

    A behavior receives the real gate authorizer, so it can attempt tool calls exactly like a
    ReAct agent would and the test observes the gate's real decisions.
    """

    def __init__(self, **behaviors: Behavior | str) -> None:
        self._behaviors = behaviors
        self.prompts: dict[str, str] = {}
        self.max_steps: list[int | None] = []
        self.parent_configs: dict[str, dict[str, Any] | None] = {}
        self.calls: list[str] = []

    async def chat(
        self,
        prompt: str,
        system_prompt: str | None = None,
        tools: list[Any] | None = None,
        session_id: str | None = None,
        user_id: str | None = None,
        trace_name: str | None = None,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        max_steps: int | None = None,
        parent_config: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> str:
        phase = next(
            (t.split(":", 1)[1] for t in (tags or []) if t.startswith("phase:")), "incident"
        )
        self.calls.append(phase)
        self.prompts[phase] = prompt
        self.parent_configs[phase] = parent_config
        self.max_steps.append(max_steps)
        behavior = self._behaviors.get(phase)
        if behavior is None and phase == "incident":
            behavior = self._behaviors.get("diagnose")
        if behavior is None:
            raise KeyError(f"No scripted behavior for phase '{phase}'")
        if isinstance(behavior, str):
            return behavior
        assert tools, "the workflow must give the agent tools"
        return await behavior(tools[0].authorizer)


class Operator:
    """Answers approval requests the way a human tapping Telegram buttons would."""

    def __init__(self, manager: ApprovalManager, *, approve: bool, reason: str = "") -> None:
        self.requests = []
        self.thread_ids: list[str | int | None] = []
        self._manager = manager
        self._approve = approve
        self._reason = reason

    async def broadcast_approval_request(
        self, request, message_thread_id=None, reply_to_message_id=None
    ) -> None:
        self.requests.append(request)
        self.thread_ids.append(message_thread_id)
        asyncio.get_running_loop().call_later(
            0.01,
            lambda: self._manager.resolve_approval(
                ApprovalResponse(
                    incident_id=request.incident_id,
                    approved=self._approve,
                    user_id="admin",
                    reason=self._reason,
                )
            ),
        )


def create_test_workflow(
    mcp_client: MCPClientInterface,
    approval_manager: ApprovalManager | None = None,
    chat_manager: ChatManager | None = None,
    llm: LLMClientInterface | None = None,
    diagnose_llm: LLMClientInterface | None = None,
    tracer: object = None,
    triage_handlers: Sequence[TriageHandler] | None = None,
    skill_matcher_service: SkillMatcherService | None = None,
) -> LyokoWorkflow:
    """Wire a LyokoWorkflow instance for tests with test doubles or mocks."""
    route_use_case = RouteEventUseCase(llm=llm)
    triage_use_case = TriageIncidentUseCase(handlers=list(triage_handlers or []))

    chat_runner = AgentRunner(mcp_client=mcp_client, llm=llm)
    chat_use_case = HandleChatTurnUseCase(
        runner=chat_runner,
        approval_manager=approval_manager,
        chat_manager=chat_manager,
    )

    incident_runner = AgentRunner(mcp_client=mcp_client, llm=diagnose_llm or llm)
    incident_use_case = ResolveIncidentUseCase(
        runner=incident_runner,
        approval_manager=approval_manager,
        chat_manager=chat_manager,
        skill_matcher_service=skill_matcher_service,
        default_chat_id="",
    )

    coordinator_use_case = CoordinateWorkflowUseCase(
        chat_use_case=chat_use_case,
        incident_use_case=incident_use_case,
    )

    enrich_trace_cost = (
        EnrichTraceCostUseCase(
            mcp_client=mcp_client,
            tracer=tracer,
            enabled=False,
        )
        if mcp_client is not None
        else None
    )

    notify_use_case = NotifyIncidentReportUseCase(
        chat_manager=chat_manager,
        enrich_trace_cost=enrich_trace_cost,
    )

    return LyokoWorkflow(
        route_use_case=route_use_case,
        triage_use_case=triage_use_case,
        coordinator_use_case=coordinator_use_case,
        notify_use_case=notify_use_case,
    )


create_lyoko_graph = create_test_workflow
