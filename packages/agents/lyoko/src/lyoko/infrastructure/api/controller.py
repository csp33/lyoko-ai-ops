"""FastAPI web controllers for webhooks and operator feedback."""

import uuid
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from lyoko.domain.interfaces.tracer import TracerInterface
from lyoko.domain.models.incident import Incident, compute_incident_key


def create_webhook_router(
    workflow_app: Any = None,
    tracer: TracerInterface | None = None,
    alert_guard: Any = None,
    task_tracker: Any = None,
) -> APIRouter:
    """Router handling Prometheus / Alertmanager incoming webhooks."""
    router = APIRouter(prefix="/webhook", tags=["webhooks"])

    @router.post("/alertmanager")
    async def handle_alertmanager_webhook(
        request: Request, background_tasks: BackgroundTasks
    ) -> dict[str, str]:
        tracker = task_tracker or getattr(request.app.state, "task_tracker", None)
        if tracker and tracker.is_closing:
            raise HTTPException(
                status_code=503, detail="Server is shutting down; draining active tasks."
            )

        try:
            data = await request.json()
        except Exception as exc:
            raise HTTPException(status_code=400, detail="Invalid JSON payload") from exc

        engine = workflow_app or getattr(request.app.state, "workflow_engine", None)
        active_tracer = tracer or getattr(request.app.state, "tracer", None)

        if not engine:
            raise HTTPException(status_code=503, detail="Workflow engine is not initialized")

        alerts = data.get("alerts", [])

        async def _dispatch(incident: Incident) -> None:
            thread_id = f"incident-{compute_incident_key(incident)}"
            trace_id = uuid.uuid4().hex
            initial_state = {
                "event_type": "alert",
                "event_id": thread_id,
                "session_id": thread_id,
                "trace_id": trace_id,
                "alert_name": incident.alert_name,
                "labels": incident.labels,
                "annotations": incident.annotations,
                "correlated_alerts": incident.correlated_alerts,
                "root_cause": "",
                "action_taken": "",
                "is_resolved": False,
                "requires_escalation": False,
            }

            trace_name = f"lyoko-{incident.alert_name}-{compute_incident_key(incident)}"

            tags = ["lyoko", f"alert:{incident.alert_name}"]
            metadata = {
                "alert_name": incident.alert_name,
                "fingerprint": incident.fingerprint or "",
            }
            if incident.namespace:
                tags.insert(1, f"ns:{incident.namespace}")
                metadata["namespace"] = incident.namespace
            if incident.pod_name:
                metadata["pod_name"] = incident.pod_name

            if active_tracer:
                config = active_tracer.get_trace_config(
                    trace_id=trace_id,
                    session_id=thread_id,
                    user_id=f"alert:{incident.alert_name}",
                    trace_name=trace_name,
                    tags=tags,
                    metadata=metadata,
                )
                config.setdefault("configurable", {})["thread_id"] = thread_id
            else:
                config = {
                    "configurable": {"thread_id": thread_id},
                    "tags": tags,
                    "metadata": metadata,
                    "run_name": trace_name,
                }

            await engine.ainvoke(initial_state, config=config)

        guard = alert_guard or getattr(request.app.state, "alert_guard", None)
        if guard is None:
            from lyoko.application.safety.alert_guard import AlertStormProtector

            chat_mgr = getattr(request.app.state, "chat_manager", None)
            guard = AlertStormProtector(
                chat_manager=chat_mgr,
                dispatch_callback=_dispatch,
                task_tracker=tracker,
            )
            # Cache on app.state if available so state is preserved across requests
            if hasattr(request.app, "state"):
                request.app.state.alert_guard = guard
        elif guard._dispatch_callback is None:
            guard._dispatch_callback = _dispatch
        if hasattr(guard, "_task_tracker") and guard._task_tracker is None and tracker is not None:
            guard._task_tracker = tracker

        background_tasks.add_task(guard.ingest_alerts, alerts)

        return {"status": "accepted", "message": f"Processing {len(alerts)} alerts in background."}

    return router
