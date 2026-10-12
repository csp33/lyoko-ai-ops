"""FastAPI web server application and lifespan management for LYOKO."""

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from lyoko.application.incidents.recovery import OrphanRecoveryService
from lyoko.application.safety.alert_guard import AlertStormProtector
from lyoko.application.safety.hitl.manager import ApprovalManager
from lyoko.application.safety.task_tracker import TaskTracker
from lyoko.application.workflow.graph import create_lyoko_graph
from lyoko.composition import (
    build_chat_manager,
    build_domain_specialists,
    build_llm_adapter,
    build_skill_matcher,
    build_supervisor,
    build_tracer,
    build_triage_handlers,
    verify_mcp_gateway,
    wire_chat_agent,
)
from lyoko.config import settings
from lyoko.domain.exceptions.mcp import MCPGatewayError
from lyoko.infrastructure.api.controller import create_feedback_router, create_webhook_router
from lyoko.infrastructure.db.lock import PostgresAdvisoryLock
from lyoko.infrastructure.db.memory_repository import PostgresMemoryRepository
from lyoko.infrastructure.embeddings import EmbeddingsService
from lyoko.infrastructure.mcp.client import FastMCPClient
from psycopg_pool import AsyncConnectionPool

logger = logging.getLogger("lyoko.web.server")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage lifecycle of database pools, checkpointers, and chat listeners."""
    logger.info("Initializing LYOKO Auto-Remediation Agent...")
    db_uri = settings.get_postgres_uri()
    pool = None
    checkpointer = None
    memory_repo = None
    embeddings_service = EmbeddingsService()

    if db_uri:
        try:
            logger.info(
                f"Connecting to PostgreSQL checkpointer at {settings.postgres_host}:{settings.postgres_port}/{settings.postgres_db}..."
            )
            pool = AsyncConnectionPool(
                conninfo=db_uri,
                max_size=settings.postgres_pool_max_size,
                kwargs={"autocommit": True, "connect_timeout": 3},
                open=False,
                timeout=5.0,
            )
            await asyncio.wait_for(pool.open(), timeout=5.0)

            # Setup LangGraph checkpointer
            checkpointer = AsyncPostgresSaver(pool)
            await asyncio.wait_for(checkpointer.setup(), timeout=5.0)
            logger.info("PostgreSQL checkpointer initialized and tables ready.")

            # Setup Memory repository & run migrations
            memory_repo = PostgresMemoryRepository(pool)
            try:
                memory_repo.run_migrations()
            except Exception as exc:
                logger.warning(f"Alembic auto-migration warning: {exc}")

            logger.info("PostgreSQL memory repository initialized.")
        except Exception as exc:
            logger.error(f"Failed to initialize PostgreSQL components: {exc}", exc_info=True)
            if pool:
                await pool.close()
                pool = None
            checkpointer = None
            memory_repo = None
    else:
        logger.warning(
            "No PostgreSQL credentials configured. LYOKO running without persistent memory & checkpointer."
        )

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
        memory_repo=memory_repo,
        embeddings_service=embeddings_service,
        lock=lock,
    )
    app.state.chat_manager = chat_manager
    wire_chat_agent(app, chat_manager)

    specialists = getattr(app.state, "specialists", None) or build_domain_specialists(
        mcp_client, llm
    )
    supervisor = getattr(app.state, "supervisor", None) or build_supervisor(specialists, llm)
    diagnose_supervisor = getattr(app.state, "diagnose_supervisor", None) or build_supervisor(
        specialists, diagnose_llm
    )

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

    # Rebuilt now that the checkpointer exists. Everything reads the graph from app.state.
    workflow_engine = create_lyoko_graph(
        mcp_client,
        approval_manager=approval_manager,
        chat_manager=chat_manager,
        checkpointer=checkpointer,
        llm=llm,
        supervisor=supervisor,
        specialists=specialists,
        memory_repository=memory_repo,
        embeddings_service=embeddings_service,
        diagnose_llm=diagnose_llm,
        diagnose_supervisor=diagnose_supervisor,
        tracer=getattr(app.state, "tracer", None),
        triage_handlers=triage_handlers,
        skill_matcher_service=skill_matcher,
    )

    app.state.triage_handlers = triage_handlers
    app.state.skill_matcher = skill_matcher
    app.state.llm = llm
    app.state.diagnose_llm = diagnose_llm
    app.state.specialists = specialists
    app.state.supervisor = supervisor
    app.state.diagnose_supervisor = diagnose_supervisor
    app.state.workflow_engine = workflow_engine
    app.state.db_pool = pool
    app.state.checkpointer = checkpointer
    app.state.memory_repository = memory_repo
    app.state.embeddings_service = embeddings_service

    if checkpointer is not None:
        recovery_svc = OrphanRecoveryService(
            workflow_engine=workflow_engine, checkpointer=checkpointer
        )
        task_tracker.track(asyncio.create_task(recovery_svc.recover_orphaned_incidents()))

    await chat_manager.start_all()
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
    specialists = build_domain_specialists(mcp_client, llm)
    supervisor = build_supervisor(specialists, llm)
    diagnose_supervisor = build_supervisor(specialists, diagnose_llm)
    alert_guard = AlertStormProtector(chat_manager=chat_manager, task_tracker=task_tracker)
    chat_manager.register_approval_handler(alert_guard.handle_force_approval)

    app = FastAPI(title="LYOKO Auto-Remediation Agent", lifespan=lifespan)

    # Initial graph (without persistent checkpointer until lifespan runs)
    workflow_engine = create_lyoko_graph(
        mcp_client,
        approval_manager=approval_manager,
        chat_manager=chat_manager,
        llm=llm,
        supervisor=supervisor,
        specialists=specialists,
        diagnose_llm=diagnose_llm,
        diagnose_supervisor=diagnose_supervisor,
        tracer=tracer,
    )
    app.state.llm = llm
    app.state.diagnose_llm = diagnose_llm
    app.state.mcp_client = mcp_client
    app.state.specialists = specialists
    app.state.supervisor = supervisor
    app.state.diagnose_supervisor = diagnose_supervisor
    app.state.approval_manager = approval_manager
    app.state.chat_manager = chat_manager
    app.state.alert_guard = alert_guard
    app.state.task_tracker = task_tracker
    app.state.workflow_engine = workflow_engine
    app.state.tracer = tracer
    app.state.checkpointer = None
    app.state.db_pool = None
    app.state.memory_repository = None
    app.state.embeddings_service = None

    wire_chat_agent(app, chat_manager)

    app.include_router(
        create_webhook_router(tracer=tracer, alert_guard=alert_guard, task_tracker=task_tracker)
    )
    app.include_router(create_feedback_router())

    @app.get("/healthz")
    @app.get("/health")
    async def health_check():
        has_db = getattr(app.state, "checkpointer", None) is not None
        has_memory = getattr(app.state, "memory_repository", None) is not None
        return {
            "status": "healthy",
            "service": "lyoko-agent",
            "postgres_checkpointer": has_db,
            "agent_memory": has_memory,
        }

    return app
