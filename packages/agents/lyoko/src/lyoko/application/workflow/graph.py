"""Autonomous execution workflow of LYOKO: event routing, triage, resolution, and notification.

Every event enters at ``route`` and takes one of two branches:
- Interactive chat messages route to the coordinator/chat handler.
- Incident alerts route through fast-path triage, then to the autonomous coordinator
  (powered by Hermes), and conclude with notification.
"""

from collections.abc import Sequence
from typing import Any

from langchain_core.runnables import RunnableConfig, RunnableLambda
from lyoko.application.agents.runner import AgentRunner
from lyoko.application.chat.use_cases.handle_turn import HandleChatTurnUseCase
from lyoko.application.incidents.triage.base import TriageHandler
from lyoko.application.incidents.triage.dispatcher import choose_triage
from lyoko.application.incidents.use_cases.enrich_trace_cost import (
    EnrichTraceCostUseCase,
)
from lyoko.application.incidents.use_cases.notify import NotifyIncidentReportUseCase
from lyoko.application.incidents.use_cases.resolve import ResolveIncidentUseCase
from lyoko.application.incidents.use_cases.triage import TriageIncidentUseCase
from lyoko.application.workflow.coordinator import CoordinateWorkflowUseCase
from lyoko.application.workflow.routing.edges import WorkflowRouteSelector
from lyoko.application.workflow.routing.route_event import RouteEventUseCase
from lyoko.config import settings
from lyoko.domain.interfaces.approval import ApprovalManagerInterface
from lyoko.domain.interfaces.chat_service import ChatServiceInterface
from lyoko.domain.interfaces.llm import LLMClientInterface
from lyoko.domain.interfaces.mcp import MCPClientInterface
from lyoko.domain.models.incident import CoordinatorNext


class LyokoWorkflow:
    """Lightweight autonomous workflow engine executing event routing, triage, and resolution."""

    def __init__(
        self,
        route_use_case: RouteEventUseCase,
        triage_use_case: TriageIncidentUseCase,
        coordinator_use_case: CoordinateWorkflowUseCase,
        notify_use_case: NotifyIncidentReportUseCase,
    ) -> None:
        self.route_use_case = route_use_case
        self.triage_use_case = triage_use_case
        self.coordinator_use_case = coordinator_use_case
        self.notify_use_case = notify_use_case

        # Wrap steps as runnables to maintain transparent child spans in tracing
        self._route_runnable = RunnableLambda(self._run_route).with_config(run_name="route")
        self._triage_runnable = RunnableLambda(self._run_triage).with_config(run_name="triage")
        self._coordinator_runnable = RunnableLambda(self._run_coordinator).with_config(
            run_name="coordinator"
        )
        self._notify_runnable = RunnableLambda(self._run_notify).with_config(run_name="notify")

    async def _run_route(
        self, state: dict[str, Any], config: RunnableConfig | None = None
    ) -> dict[str, Any]:
        return await self.route_use_case.execute(state, config=config)

    async def _run_triage(
        self, state: dict[str, Any], config: RunnableConfig | None = None
    ) -> dict[str, Any]:
        return await self.triage_use_case.execute(state)

    async def _run_coordinator(
        self, state: dict[str, Any], config: RunnableConfig | None = None
    ) -> dict[str, Any]:
        return await self.coordinator_use_case.execute(state, config=config)

    async def _run_notify(
        self, state: dict[str, Any], config: RunnableConfig | None = None
    ) -> dict[str, Any]:
        return await self.notify_use_case.execute(state)

    async def _execute_flow(
        self, state: dict[str, Any], config: RunnableConfig | None = None
    ) -> dict[str, Any]:
        current_state = dict(state or {})

        # Step 1: Route
        route_patch = await self._route_runnable.ainvoke(current_state, config=config)
        current_state.update(route_patch)
        branch = WorkflowRouteSelector.choose_branch(current_state)

        if branch == "diagnose":
            # Step 2: Triage (fast-path deterministic handlers)
            triage_patch = await self._triage_runnable.ainvoke(current_state, config=config)
            current_state.update(triage_patch)
            if choose_triage(current_state) == "handled":
                notify_patch = await self._notify_runnable.ainvoke(current_state, config=config)
                current_state.update(notify_patch)
                return current_state

        # Step 3: Coordinator / Resolver (chat interaction or autonomous Hermes resolution)
        coord_patch = await self._coordinator_runnable.ainvoke(current_state, config=config)
        current_state.update(coord_patch)

        next_action = WorkflowRouteSelector.choose_coordinator_next(current_state)
        if next_action == CoordinatorNext.CHAT_END:
            return current_state

        # Step 4: Notify for incident resolution
        notify_patch = await self._notify_runnable.ainvoke(current_state, config=config)
        current_state.update(notify_patch)
        return current_state

    async def ainvoke(
        self, state: dict[str, Any] | None, config: RunnableConfig | None = None
    ) -> dict[str, Any]:
        """Execute the workflow asynchronously with hierarchical tracing."""
        runnable = RunnableLambda(self._execute_flow)
        return await runnable.ainvoke(state or {}, config=config)


def create_lyoko_graph(
    mcp_client: MCPClientInterface,
    approval_manager: ApprovalManagerInterface | None = None,
    chat_manager: ChatServiceInterface | None = None,
    llm: LLMClientInterface | None = None,
    diagnose_llm: LLMClientInterface | None = None,
    tracer: Any = None,
    triage_handlers: Sequence[TriageHandler] | None = None,
    skill_matcher_service: Any = None,
    enrich_trace_cost: EnrichTraceCostUseCase | None = None,
    incident_use_case: ResolveIncidentUseCase | Any | None = None,
    chat_use_case: HandleChatTurnUseCase | Any | None = None,
) -> LyokoWorkflow:
    """Build the LYOKO autonomous workflow that routes, triages, resolves, and notifies."""
    route_use_case = RouteEventUseCase(llm=llm)
    handlers = triage_handlers if triage_handlers is not None else []
    triage_use_case = TriageIncidentUseCase(handlers=handlers)

    if chat_use_case is None:
        chat_runner = AgentRunner(mcp_client=mcp_client, llm=llm)
        chat_use_case = HandleChatTurnUseCase(
            runner=chat_runner,
            approval_manager=approval_manager,
            chat_manager=chat_manager,
        )

    if incident_use_case is None:
        incident_runner = AgentRunner(mcp_client=mcp_client, llm=diagnose_llm or llm)
        incident_use_case = ResolveIncidentUseCase(
            runner=incident_runner,
            approval_manager=approval_manager,
            chat_manager=chat_manager,
            skill_matcher_service=skill_matcher_service,
            default_chat_id=settings.telegram_default_chat_id or "",
        )

    coordinator_use_case = CoordinateWorkflowUseCase(
        chat_use_case=chat_use_case,
        incident_use_case=incident_use_case,
    )
    if enrich_trace_cost is None and mcp_client is not None:
        enrich_trace_cost = EnrichTraceCostUseCase(
            mcp_client=mcp_client,
            tracer=tracer,
            enabled=settings.telegram_show_trace_cost,
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
