"""LangGraph StateGraph of LYOKO: one graph, two branches.

Every event enters at ``route`` and takes one of two branches::

    START -> route -+-> chat                                      -> END
                    +-> diagnose -> remediate -> verify -> notify -> END

* ``chat`` answers an operator message, or carries out a request, in a single tool-using run.
* The incident branch investigates, fixes, verifies and reports. Alertmanager alerts always
  take it. A Telegram message takes it when the router decides the operator is reporting
  something broken, and takes ``chat`` otherwise.

The agent runs use the sector5-mcp gateway through domain specialists. The supervisor
delegates to Kubernetes, UniFi, Home Assistant, and Grafana specialists so each phase
operates with a scoped toolset instead of searching the whole catalog.

Safety does not rely on the agent's judgement. Every run gets a ``ToolGate`` built from one
policy for both branches: read-only tools run, auto-approved tools run, and everything else waits
for the operator. Diagnosis and verification are stricter still and cannot change anything.

Every agent run receives the node's run config, so one event produces one trace in which each
node and each agent run is a named child span.
"""

from collections.abc import Sequence
from typing import Any

from langgraph.graph import END, START, StateGraph
from lyoko.application.agents.task_runner import ExecuteSpecialistTaskUseCase
from lyoko.application.incidents.triage.base import TriageHandler
from lyoko.application.incidents.triage.dispatcher import choose_triage
from lyoko.application.incidents.use_cases.notify import NotifyIncidentReportUseCase
from lyoko.application.incidents.use_cases.remediate import RemediateIncidentUseCase
from lyoko.application.incidents.use_cases.triage import TriageIncidentUseCase
from lyoko.application.incidents.use_cases.verify import VerifyIncidentUseCase
from lyoko.application.workflow.coordinator import CoordinateWorkflowUseCase
from lyoko.application.workflow.routing.edges import WorkflowRouteSelector
from lyoko.application.workflow.routing.route_event import RouteEventUseCase
from lyoko.config import settings  # noqa: F401
from lyoko.domain.interfaces.approval import ApprovalManagerInterface
from lyoko.domain.interfaces.chat_service import ChatServiceInterface
from lyoko.domain.interfaces.embeddings import EmbeddingsServiceInterface
from lyoko.domain.interfaces.llm import LLMClientInterface
from lyoko.domain.interfaces.mcp import MCPClientInterface
from lyoko.domain.interfaces.memory import MemoryRepositoryInterface
from lyoko.domain.interfaces.supervisor import SupervisorInterface
from lyoko.domain.models.incident import CoordinatorNext, SpecialistDomain
from lyoko.domain.models.state import LyokoState


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
) -> Any:
    """Build the LangGraph StateGraph that routes, answers, investigates and remediates.

    ``diagnose_llm``/``diagnose_supervisor`` optionally give the diagnose phase a different model
    than the rest of the graph; both fall back to ``llm``/``supervisor`` when omitted.
    """
    route_use_case = RouteEventUseCase(llm=llm)
    handlers = triage_handlers if triage_handlers is not None else []
    triage_use_case = TriageIncidentUseCase(handlers=handlers)

    coordinator_use_case = CoordinateWorkflowUseCase(
        mcp_client=mcp_client,
        llm=llm,
        supervisor=supervisor,
        approval_manager=approval_manager,
        chat_manager=chat_manager,
        memory_repository=memory_repository,
        embeddings_service=embeddings_service,
        diagnose_llm=diagnose_llm,
        diagnose_supervisor=diagnose_supervisor,
        skill_matcher_service=skill_matcher_service,
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
    notify_use_case = NotifyIncidentReportUseCase(chat_manager=chat_manager)

    workflow = StateGraph(LyokoState)
    workflow.add_node("route", route_use_case.execute)
    workflow.add_node("triage", triage_use_case.execute)
    workflow.add_node("coordinator", coordinator_use_case.execute)

    # Specialist nodes
    specialists_dict = specialists or {}
    for domain in SpecialistDomain:
        spec = specialists_dict.get(domain)
        specialist_use_case = ExecuteSpecialistTaskUseCase(
            domain=domain,
            specialist=spec,
            mcp_client=mcp_client,
            approval_manager=approval_manager,
            chat_manager=chat_manager,
        )
        workflow.add_node(domain, specialist_use_case.execute)
        workflow.add_edge(domain, "coordinator")

    workflow.add_node("remediate", remediate_use_case.execute)
    workflow.add_node("verify", verify_use_case.execute)
    workflow.add_node("notify", notify_use_case.execute)

    workflow.add_edge(START, "route")
    workflow.add_conditional_edges(
        "route",
        WorkflowRouteSelector.choose_branch,
        {"chat": "coordinator", "diagnose": "triage"},
    )
    workflow.add_conditional_edges(
        "triage",
        choose_triage,
        {"handled": "notify", "diagnose": "coordinator"},
    )
    workflow.add_conditional_edges(
        "coordinator",
        WorkflowRouteSelector.choose_coordinator_next,
        {
            CoordinatorNext.KUBERNETES: SpecialistDomain.KUBERNETES,
            CoordinatorNext.UNIFI: SpecialistDomain.UNIFI,
            CoordinatorNext.HOMEASSISTANT: SpecialistDomain.HOMEASSISTANT,
            CoordinatorNext.GRAFANA: SpecialistDomain.GRAFANA,
            CoordinatorNext.REMEDIATE: "remediate",
            CoordinatorNext.CHAT_END: END,
        },
    )
    workflow.add_edge("remediate", "verify")
    workflow.add_edge("verify", "notify")
    workflow.add_edge("notify", END)

    return workflow.compile(checkpointer=checkpointer)
