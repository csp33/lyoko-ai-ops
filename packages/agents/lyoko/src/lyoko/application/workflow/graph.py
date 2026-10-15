"""Autonomous execution workflow of LYOKO: event routing, triage, resolution, and notification.

Every event enters at ``route`` and takes one of two branches:
- Interactive chat messages route to the coordinator/chat handler.
- Incident alerts route through fast-path triage, then to the autonomous coordinator
  (powered by Hermes), and conclude with notification.
"""

from typing import Any

from langchain_core.runnables import RunnableConfig, RunnableLambda
from lyoko.application.incidents.triage.dispatcher import choose_triage
from lyoko.application.incidents.use_cases.notify import NotifyIncidentReportUseCase
from lyoko.application.incidents.use_cases.triage import TriageIncidentUseCase
from lyoko.application.workflow.coordinator import CoordinateWorkflowUseCase
from lyoko.application.workflow.routing.edges import WorkflowRouteSelector
from lyoko.application.workflow.routing.route_event import RouteEventUseCase
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
