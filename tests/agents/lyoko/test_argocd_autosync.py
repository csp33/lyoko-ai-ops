"""Tests for the deterministic Argo CD autosync fast path."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
import yaml
from lyoko.application.incidents.triage.argocd_autosync import ArgoCDAutosyncHandler
from lyoko.application.incidents.triage.argocd_inspector import ArgoCDApplicationInspector
from lyoko.application.incidents.triage.dispatcher import (
    TRIAGE_DIAGNOSE,
    TRIAGE_HANDLED,
    choose_triage,
)
from lyoko.application.incidents.use_cases.triage import TriageIncidentUseCase
from lyoko.config import settings

autosync_disabled = ArgoCDApplicationInspector.autosync_disabled
enable_autosync = ArgoCDApplicationInspector.enable_autosync
has_sync_error = ArgoCDApplicationInspector.has_sync_error
is_synced_and_healthy = ArgoCDApplicationInspector.is_synced_and_healthy
parse_outofsync_app = ArgoCDApplicationInspector.parse_outofsync_app


def create_argocd_autosync_node(
    mcp_client=None,
    approval_manager=None,
    chat_manager=None,
    tracer=None,
):
    handler = ArgoCDAutosyncHandler(
        mcp_client=mcp_client,
        approval_manager=approval_manager,
        chat_manager=chat_manager,
        tracer=tracer,
    )
    return TriageIncidentUseCase(handlers=[handler]).execute


_ALERT = "🔔 Alert· Argo CD application arr-stack has sync status OutOfSync for more than 15m."


def _app_yaml(enabled: bool | None, sync: str, health: str, phase: str = "Succeeded") -> str:
    spec: dict = {
        "destination": {"namespace": "media-stack"},
        "source": {"helm": {"path": "charts/arr-stack"}},
    }
    if enabled is not None:
        spec["syncPolicy"] = {"automated": {"enabled": enabled, "prune": True, "selfHeal": True}}
    manifest = {
        "apiVersion": "argoproj.io/v1alpha1",
        "kind": "Application",
        "metadata": {"name": "arr-stack", "namespace": "argocd"},
        "spec": spec,
        "status": {
            "sync": {"status": sync},
            "health": {"status": health},
            "operationState": {"phase": phase},
        },
    }
    return yaml.safe_dump(manifest, sort_keys=False)


def _approval_manager(approved: bool = True) -> MagicMock:
    manager = MagicMock()
    manager.wait_for_approval = AsyncMock(
        return_value=SimpleNamespace(approved=approved, reason="")
    )
    return manager


def _chat_manager() -> MagicMock:
    manager = MagicMock()
    manager.broadcast_approval_request = AsyncMock()
    return manager


def test_parse_outofsync_app_without_regex():
    assert parse_outofsync_app(_ALERT) == "arr-stack"
    assert (
        parse_outofsync_app("Argo CD application `bentopdf` has sync status OutOfSync")
        == "bentopdf"
    )
    assert parse_outofsync_app("radarr keeps crashing") is None
    assert parse_outofsync_app("Argo CD app arr-stack is OutOfSync") is None
    assert parse_outofsync_app("") is None


def test_manifest_helpers():
    disabled = yaml.safe_load(_app_yaml(False, "OutOfSync", "Healthy"))
    absent = yaml.safe_load(_app_yaml(None, "OutOfSync", "Healthy"))
    enabled = yaml.safe_load(_app_yaml(True, "Synced", "Healthy"))
    failing = yaml.safe_load(_app_yaml(False, "OutOfSync", "Healthy", phase="Failed"))

    assert autosync_disabled(disabled) is True
    assert autosync_disabled(absent) is True
    assert autosync_disabled(enabled) is False
    assert has_sync_error(failing) is True
    assert has_sync_error(disabled) is False
    assert is_synced_and_healthy(enabled) is True

    merged = enable_autosync(disabled)
    assert autosync_disabled(merged) is False


def test_choose_triage():
    assert choose_triage({"triage": TRIAGE_HANDLED}) == TRIAGE_HANDLED
    assert choose_triage({}) == TRIAGE_DIAGNOSE
    assert choose_triage({"triage": "diagnose"}) == TRIAGE_DIAGNOSE


@pytest.mark.asyncio
async def test_autosync_node_enables_autosync_end_to_end(monkeypatch):
    monkeypatch.setattr(settings, "verification_delay_seconds", 0)
    mcp = MagicMock()
    mcp.call_tool = AsyncMock(
        side_effect=[
            _app_yaml(False, "OutOfSync", "Healthy"),
            {"status": "success"},
            _app_yaml(True, "Synced", "Healthy"),
        ]
    )
    node = create_argocd_autosync_node(
        mcp_client=mcp,
        approval_manager=_approval_manager(True),
        chat_manager=_chat_manager(),
    )
    state = {"event_type": "message", "text": _ALERT, "event_id": "e1", "chat_id": "-100"}

    result = await node(state, {})

    assert result["triage"] == TRIAGE_HANDLED
    assert result["is_resolved"] is True
    assert result["requires_escalation"] is False
    assert "autosync" in result["action_taken"].lower()
    assert any(record["outcome"] == "approved" for record in result["actions"])
    # First read, apply, verify read.
    called = [call.args[0] for call in mcp.call_tool.await_args_list]
    assert called == ["k8s_resources_get", "k8s_resources_create_or_update", "k8s_resources_get"]


@pytest.mark.asyncio
async def test_autosync_node_waits_for_argo_to_reconcile(monkeypatch):
    """Argo reconciles asynchronously: keep polling until Synced instead of escalating early."""
    monkeypatch.setattr(settings, "verification_delay_seconds", 0)
    mcp = MagicMock()
    mcp.call_tool = AsyncMock(
        side_effect=[
            _app_yaml(False, "OutOfSync", "Healthy"),
            {"status": "success"},
            _app_yaml(True, "OutOfSync", "Healthy"),  # not reconciled yet
            _app_yaml(True, "OutOfSync", "Progressing"),  # still reconciling
            _app_yaml(True, "Synced", "Healthy"),  # converged
        ]
    )
    node = create_argocd_autosync_node(
        mcp_client=mcp,
        approval_manager=_approval_manager(True),
        chat_manager=_chat_manager(),
    )
    state = {"event_type": "message", "text": _ALERT, "event_id": "e1", "chat_id": "-100"}

    result = await node(state, {})

    assert result["is_resolved"] is True
    assert result["requires_escalation"] is False
    assert mcp.call_tool.await_count == 5


@pytest.mark.asyncio
async def test_autosync_node_emits_trace_spans(monkeypatch):
    """The fast path calls infrastructure directly, so it wraps steps in named traced runs."""
    monkeypatch.setattr(settings, "verification_delay_seconds", 0)

    class _SpanTracer:
        def __init__(self) -> None:
            self.spans: list[str] = []

        async def traced(self, name, run):  # noqa: ANN001
            self.spans.append(name)
            return await run()

    mcp = MagicMock()
    mcp.call_tool = AsyncMock(
        side_effect=[
            _app_yaml(False, "OutOfSync", "Healthy"),
            {"status": "success"},
            _app_yaml(True, "Synced", "Healthy"),
        ]
    )
    tracer = _SpanTracer()
    node = create_argocd_autosync_node(
        mcp_client=mcp,
        approval_manager=_approval_manager(True),
        chat_manager=_chat_manager(),
        tracer=tracer,
    )
    state = {"event_type": "message", "text": _ALERT, "event_id": "e1", "chat_id": "-100"}

    await node(state, {})

    assert "mcp:k8s_resources_get" in tracer.spans
    assert "mcp:k8s_resources_create_or_update" in tracer.spans
    assert "operator-approval" in tracer.spans


@pytest.mark.asyncio
async def test_autosync_node_aborts_when_denied():
    mcp = MagicMock()
    mcp.call_tool = AsyncMock(return_value=_app_yaml(False, "OutOfSync", "Healthy"))
    node = create_argocd_autosync_node(
        mcp_client=mcp,
        approval_manager=_approval_manager(False),
        chat_manager=_chat_manager(),
    )
    state = {"event_type": "message", "text": _ALERT, "event_id": "e1", "chat_id": "-100"}

    result = await node(state, {})

    assert result["triage"] == TRIAGE_HANDLED
    assert result["requires_escalation"] is True
    assert result["is_resolved"] is False
    assert "aborted" in result["action_taken"].lower()
    # No apply: only the initial read happened.
    assert mcp.call_tool.await_count == 1


@pytest.mark.asyncio
async def test_autosync_node_falls_through_when_not_applicable():
    mcp = MagicMock()
    mcp.call_tool = AsyncMock(return_value=_app_yaml(True, "Synced", "Healthy"))
    node = create_argocd_autosync_node(mcp_client=mcp)

    enabled = await node(
        {"event_type": "message", "text": _ALERT, "event_id": "e1", "chat_id": "-100"}, {}
    )
    assert enabled == {"triage": TRIAGE_DIAGNOSE}

    other = await node(
        {"event_type": "message", "text": "radarr keeps crashing", "event_id": "e2"}, {}
    )
    assert other == {"triage": TRIAGE_DIAGNOSE}

    # An alert event with matching alert_name enters the fast path.
    alert_matching = await node(
        {
            "event_type": "alert",
            "alert_name": "ArgoCDAppOutOfSync",
            "labels": {"name": "arr-stack"},
            "event_id": "e3",
        },
        {},
    )
    # Since mock returns Synced & Healthy, it is not applicable for autosync enable and falls through to diagnose
    assert alert_matching == {"triage": TRIAGE_DIAGNOSE}

    # An unknown alert event falls through directly without checking
    alert_other = await node(
        {"event_type": "alert", "alert_name": "OtherAlert", "event_id": "e4"}, {}
    )
    assert alert_other == {"triage": TRIAGE_DIAGNOSE}
