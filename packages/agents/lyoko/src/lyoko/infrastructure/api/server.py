"""FastAPI web server application and lifespan management for LYOKO."""

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from lyoko.application.incidents.use_cases.resolve import ResolveIncidentUseCase
from lyoko.application.safety.alert_guard import AlertStormProtector
from lyoko.application.safety.gated_run_context import get_gated_run_context
from lyoko.application.safety.hitl.manager import ApprovalManager
from lyoko.application.safety.task_tracker import TaskTracker
from lyoko.application.workflow.graph import create_lyoko_graph
from lyoko.composition import (
    build_chat_manager,
    build_llm_adapter,
    build_skill_matcher,
    build_tracer,
    build_triage_handlers,
    verify_mcp_gateway,
    wire_chat_agent,
)
from lyoko.config import settings
from lyoko.domain.exceptions.mcp import MCPGatewayError
from lyoko.infrastructure.api.controller import create_webhook_router
from lyoko.infrastructure.db.lock import PostgresAdvisoryLock
from lyoko.infrastructure.mcp.client import FastMCPClient
from lyoko.infrastructure.mcp.gated_proxy import GATED_MCP_MOUNT_PATH, build_gated_mcp_app
from psycopg_pool import AsyncConnectionPool

logger = logging.getLogger("lyoko.web.server")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage lifecycle of database pools, chat listeners, and Hermes agent."""
    logger.info("Initializing LYOKO Auto-Remediation Agent...")
    db_uri = settings.get_postgres_uri()
    pool = None

    if db_uri:
        try:
            logger.info(
                f"Connecting to PostgreSQL at {settings.postgres_host}:{settings.postgres_port}/{settings.postgres_db}..."
            )
            pool = AsyncConnectionPool(
                conninfo=db_uri,
                max_size=settings.postgres_pool_max_size,
                kwargs={"autocommit": True, "connect_timeout": 3},
                check=AsyncConnectionPool.check_connection,
                max_idle=300.0,
                open=False,
                timeout=5.0,
            )
            await asyncio.wait_for(pool.open(), timeout=5.0)
            logger.info("PostgreSQL connection pool initialized for distributed locks.")
        except Exception as exc:
            logger.error(f"Failed to initialize PostgreSQL components: {exc}", exc_info=True)
            if pool:
                await pool.close()
                pool = None
    else:
        logger.info("No PostgreSQL credentials configured. Running without multi-pod leader lock.")

    llm = getattr(app.state, "llm", None) or build_llm_adapter()
    diagnose_llm = getattr(app.state, "diagnose_llm", None) or build_llm_adapter(
        settings.openai_diagnose_model or None
    )
    mcp_client = getattr(app.state, "mcp_client", None) or FastMCPClient()
    approval_manager = getattr(app.state, "approval_manager", None) or ApprovalManager()
    try:
        await verify_mcp_gateway(mcp_client)
    except MCPGatewayError:
        if pool:
            await pool.close()
        raise
    lock = PostgresAdvisoryLock(pool=pool) if pool else None
    chat_manager = build_chat_manager(
        approval_manager,
        lock=lock,
    )
    app.state.chat_manager = chat_manager
    wire_chat_agent(app, chat_manager)

    task_tracker = getattr(app.state, "task_tracker", None) or TaskTracker()
    app.state.task_tracker = task_tracker

    alert_guard = AlertStormProtector(chat_manager=chat_manager, task_tracker=task_tracker)
    chat_manager.register_approval_handler(alert_guard.handle_force_approval)
    app.state.alert_guard = alert_guard

    triage_handlers = getattr(app.state, "triage_handlers", None) or build_triage_handlers(
        mcp_client=mcp_client,
        approval_manager=approval_manager,
        chat_manager=chat_manager,
        tracer=getattr(app.state, "tracer", None),
    )

    skill_matcher = getattr(app.state, "skill_matcher", None) or build_skill_matcher()

    incident_use_case = ResolveIncidentUseCase(
        mcp_client=mcp_client,
        llm=diagnose_llm or llm,
        approval_manager=approval_manager,
        chat_manager=chat_manager,
        skill_matcher_service=skill_matcher,
        default_chat_id=settings.telegram_default_chat_id or "",
    )

    workflow_engine = create_lyoko_graph(
        mcp_client,
        approval_manager=approval_manager,
        chat_manager=chat_manager,
        llm=llm,
        diagnose_llm=diagnose_llm,
        tracer=getattr(app.state, "tracer", None),
        triage_handlers=triage_handlers,
        skill_matcher_service=skill_matcher,
        incident_use_case=incident_use_case,
    )

    app.state.triage_handlers = triage_handlers
    app.state.skill_matcher = skill_matcher
    app.state.llm = llm
    app.state.diagnose_llm = diagnose_llm
    app.state.workflow_engine = workflow_engine
    app.state.db_pool = pool

    await chat_manager.start_all()
    gated_inner = getattr(app.state, "gated_mcp_inner", None)
    if gated_inner is not None:
        async with gated_inner.router.lifespan_context(gated_inner):
            yield
    else:
        yield
    logger.info("Shutting down LYOKO Agent...")
    await chat_manager.stop_all()
    await task_tracker.drain(timeout=settings.shutdown_drain_timeout_seconds)
    if pool:
        await pool.close()


def create_app() -> FastAPI:
    """Create and configure the FastAPI application instance."""
    llm = build_llm_adapter()
    diagnose_llm = build_llm_adapter(settings.openai_diagnose_model or None)
    mcp_client = FastMCPClient()
    approval_manager = ApprovalManager()
    chat_manager = build_chat_manager(approval_manager)
    tracer = build_tracer()
    task_tracker = TaskTracker()
    alert_guard = AlertStormProtector(chat_manager=chat_manager, task_tracker=task_tracker)
    chat_manager.register_approval_handler(alert_guard.handle_force_approval)

    app = FastAPI(title="LYOKO Auto-Remediation Agent", lifespan=lifespan)

    app.state.llm = llm
    app.state.diagnose_llm = diagnose_llm
    app.state.mcp_client = mcp_client
    app.state.approval_manager = approval_manager
    app.state.chat_manager = chat_manager
    app.state.alert_guard = alert_guard
    app.state.task_tracker = task_tracker
    app.state.workflow_engine = None
    app.state.tracer = tracer
    app.state.db_pool = None

    wire_chat_agent(app, chat_manager)

    app.include_router(
        create_webhook_router(tracer=tracer, alert_guard=alert_guard, task_tracker=task_tracker)
    )

    app.state.gated_mcp_inner = None
    if settings.gated_mcp_token:
        guarded, inner = build_gated_mcp_app(get_gated_run_context(), settings.gated_mcp_token)
        app.mount(GATED_MCP_MOUNT_PATH, guarded)
        app.state.gated_mcp_inner = inner
    else:
        logger.warning("LYOKO_GATED_MCP_TOKEN is not set: gated MCP proxy disabled (fail closed).")

    @app.get("/healthz")
    @app.get("/health")
    async def health_check():
        has_db = getattr(app.state, "db_pool", None) is not None
        return {
            "status": "healthy",
            "service": "lyoko-agent",
            "database": has_db,
        }

    return app
