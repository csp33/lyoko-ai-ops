"""Autonomous execution workflow of LYOKO: event routing, triage, and resolution.

Every event enters at ``route`` and takes one of two branches:
- Interactive chat messages route to the coordinator/chat handler.
- Incident alerts route through fast-path triage, then to the autonomous coordinator
  (powered by Hermes), and conclude with notification.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from langchain_core.runnables import RunnableConfig, RunnableLambda
from lyoko.application.agents.task_runner import ExecuteSpecialistTaskUseCase
from lyoko.application.incidents.triage.base import TriageHandler
from lyoko.application.incidents.triage.dispatcher import choose_triage
from lyoko.application.incidents.use_cases.enrich_trace_cost import (
    EnrichTraceCostUseCase,
)
from lyoko.application.incidents.use_cases.notify import NotifyIncidentReportUseCase
from lyoko.application.incidents.use_cases.remediate import RemediateIncidentUseCase
from lyoko.application.incidents.use_cases.triage import TriageIncidentUseCase
from lyoko.application.incidents.use_cases.verify import VerifyIncidentUseCase
from lyoko.application.workflow.coordinator import CoordinateWorkflowUseCase
from lyoko.application.workflow.routing.edges import WorkflowRouteSelector
from lyoko.application.workflow.routing.route_event import RouteEventUseCase
from lyoko.config import settings
from lyoko.domain.interfaces.approval import ApprovalManagerInterface
from lyoko.domain.interfaces.chat_service import ChatServiceInterface
from lyoko.domain.interfaces.embeddings import EmbeddingsServiceInterface
from lyoko.domain.interfaces.llm import LLMClientInterface
from lyoko.domain.interfaces.mcp import MCPClientInterface
from lyoko.domain.interfaces.memory import MemoryRepositoryInterface
from lyoko.domain.interfaces.supervisor import SupervisorInterface
from lyoko.domain.models.incident import CoordinatorNext, SpecialistDomain


@dataclass
class WorkflowStateSnapshot:
    """Snapshot of workflow state for state inspection and recovery."""

    values: dict[str, Any]
    next: tuple[str, ...] = field(default_factory=tuple)


class LyokoWorkflow:
    """Lightweight autonomous workflow engine executing event routing, triage, and resolution."""

    def __init__(
        self,
        route_use_case: RouteEventUseCase,
        triage_use_case: TriageIncidentUseCase,
        coordinator_use_case: CoordinateWorkflowUseCase,
        remediate_use_case: RemediateIncidentUseCase,
        verify_use_case: VerifyIncidentUseCase,
        notify_use_case: NotifyIncidentReportUseCase,
        specialist_use_cases: dict[str, ExecuteSpecialistTaskUseCase] | None = None,
        checkpointer: Any = None,
    ) -> None:
        self.route_use_case = route_use_case
        self.triage_use_case = triage_use_case
        self.coordinator_use_case = coordinator_use_case
        self.remediate_use_case = remediate_use_case
        self.verify_use_case = verify_use_case
        self.notify_use_case = notify_use_case
        self.specialist_use_cases = specialist_use_cases or {}
        self.checkpointer = checkpointer
        self._saved_states: dict[str, WorkflowStateSnapshot] = {}

        # Wrap steps as runnables to maintain transparent child spans in tracing
        self._route_runnable = RunnableLambda(self._run_route).with_config(run_name="route")
        self._triage_runnable = RunnableLambda(self._run_triage).with_config(run_name="triage")
        self._coordinator_runnable = RunnableLambda(self._run_coordinator).with_config(
            run_name="coordinator"
        )
        self._remediate_runnable = RunnableLambda(self._run_remediate).with_config(
            run_name="remediate"
        )
        self._verify_runnable = RunnableLambda(self._run_verify).with_config(run_name="verify")
        self._notify_runnable = RunnableLambda(self._run_notify).with_config(run_name="notify")
        self._specialist_runnables = {
            domain: RunnableLambda(uc.execute).with_config(run_name=str(domain))
            for domain, uc in self.specialist_use_cases.items()
        }

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

    async def _run_remediate(
        self, state: dict[str, Any], config: RunnableConfig | None = None
    ) -> dict[str, Any]:
        return await self.remediate_use_case.execute(state, config=config)

    async def _run_verify(
        self, state: dict[str, Any], config: RunnableConfig | None = None
    ) -> dict[str, Any]:
        return await self.verify_use_case.execute(state, config=config)

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

        # Step 3: Coordinator & execution loop
        while True:
            coord_patch = await self._coordinator_runnable.ainvoke(current_state, config=config)
            current_state.update(coord_patch)

            next_action = WorkflowRouteSelector.choose_coordinator_next(current_state)
            if next_action == CoordinatorNext.CHAT_END:
                return current_state
            if next_action in self._specialist_runnables:
                spec_patch = await self._specialist_runnables[next_action].ainvoke(
                    current_state, config=config
                )
                current_state.update(spec_patch)
                continue
            if next_action == CoordinatorNext.REMEDIATE:
                rem_patch = await self._remediate_runnable.ainvoke(current_state, config=config)
                current_state.update(rem_patch)
                ver_patch = await self._verify_runnable.ainvoke(current_state, config=config)
                current_state.update(ver_patch)
                notify_patch = await self._notify_runnable.ainvoke(current_state, config=config)
                current_state.update(notify_patch)
                return current_state
            if next_action == CoordinatorNext.NOTIFY:
                notify_patch = await self._notify_runnable.ainvoke(current_state, config=config)
                current_state.update(notify_patch)
                return current_state
            break

        return current_state

    async def ainvoke(
        self, state: dict[str, Any] | None, config: RunnableConfig | None = None
    ) -> dict[str, Any]:
        """Execute the workflow asynchronously with hierarchical tracing and state recording."""
        runnable = RunnableLambda(self._execute_flow)
        result = await runnable.ainvoke(state or {}, config=config)

        if isinstance(config, dict):
            thread_id = config.get("configurable", {}).get("thread_id")
            if thread_id:
                self._saved_states[thread_id] = WorkflowStateSnapshot(values=dict(result))

        return result

    async def aget_state(
        self, config: RunnableConfig | None = None
    ) -> WorkflowStateSnapshot | None:
        """Retrieve the latest recorded state snapshot for a given thread_id."""
        if isinstance(config, dict):
            thread_id = config.get("configurable", {}).get("thread_id")
            if thread_id:
                return self._saved_states.get(thread_id)
        return None


def create_lyoko_graph(
    mcp_client: MCPClientInterface,
    approval_manager: ApprovalManagerInterface | None = None,
    chat_manager: ChatServiceInterface | None = None,
    checkpointer: Any = None,
    llm: LLMClientInterface | None = None,
    supervisor: SupervisorInterface | None = None,
    specialists: dict[str, Any] | None = None,
    memory_repository: MemoryRepositoryInterface | None = None,
    embeddings_service: EmbeddingsServiceInterface | None = None,
    diagnose_llm: LLMClientInterface | None = None,
    diagnose_supervisor: SupervisorInterface | None = None,
    tracer: Any = None,
    triage_handlers: Sequence[TriageHandler] | None = None,
    skill_matcher_service: Any = None,
    enrich_trace_cost: EnrichTraceCostUseCase | None = None,
    incident_use_case: Any | None = None,
) -> LyokoWorkflow:
    """Build the LYOKO autonomous workflow that routes, triages, resolves, and notifies."""
    route_use_case = RouteEventUseCase(llm=llm)
    handlers = triage_handlers if triage_handlers is not None else []
    triage_use_case = TriageIncidentUseCase(handlers=handlers)

    # Memory persistence is fully delegated to Hermes native memory.
    graph_memory_repo = None
    graph_embeddings_service = None

    coordinator_use_case = CoordinateWorkflowUseCase(
        mcp_client=mcp_client,
        llm=llm,
        supervisor=supervisor,
        approval_manager=approval_manager,
        chat_manager=chat_manager,
        memory_repository=graph_memory_repo,
        embeddings_service=graph_embeddings_service,
        diagnose_llm=diagnose_llm,
        diagnose_supervisor=diagnose_supervisor,
        skill_matcher_service=skill_matcher_service,
        incident_use_case=incident_use_case,
    )
    remediate_use_case = RemediateIncidentUseCase(
        mcp_client=mcp_client,
        llm=llm,
        supervisor=supervisor,
        approval_manager=approval_manager,
        chat_manager=chat_manager,
    )
    verify_use_case = VerifyIncidentUseCase(
        mcp_client=mcp_client,
        llm=llm,
        supervisor=supervisor,
        approval_manager=approval_manager,
        chat_manager=chat_manager,
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

    specialists_dict = specialists or {}
    specialist_use_cases = {}
    for domain in SpecialistDomain:
        spec = specialists_dict.get(domain)
        specialist_use_cases[CoordinatorNext(domain)] = ExecuteSpecialistTaskUseCase(
            domain=domain,
            specialist=spec,
            mcp_client=mcp_client,
            approval_manager=approval_manager,
            chat_manager=chat_manager,
        )

    return LyokoWorkflow(
        route_use_case=route_use_case,
        triage_use_case=triage_use_case,
        coordinator_use_case=coordinator_use_case,
        remediate_use_case=remediate_use_case,
        verify_use_case=verify_use_case,
        notify_use_case=notify_use_case,
        specialist_use_cases=specialist_use_cases,
        checkpointer=checkpointer,
    )
