"""Tests for deterministic KubePodCrashLooping triage handler."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from lyoko.application.incidents.triage.pod_crashloop import PodCrashLoopHandler


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


def test_can_handle_detects_alert_and_message():
    handler = PodCrashLoopHandler(mcp_client=MagicMock())

    alert_state = {
        "event_type": "alert",
        "alert_name": "KubePodCrashLooping",
        "labels": {"namespace": "media", "pod": "radarr-xxx", "container": "radarr"},
    }
    assert handler.can_handle(alert_state) is True

    msg_state = {
        "event_type": "message",
        "text": "pod radarr-xxx in media is crashlooping, fix it",
    }
    assert handler.can_handle(msg_state) is True

    unknown_state = {
        "event_type": "alert",
        "alert_name": "SomeOtherAlert",
        "labels": {},
    }
    assert handler.can_handle(unknown_state) is False


@pytest.mark.asyncio
async def test_execute_handles_crashloop_with_approval(monkeypatch):
    from lyoko.config import settings

    monkeypatch.setattr(settings, "verification_delay_seconds", 0)

    mcp = MagicMock()
    # 1. k8s_pods_delete succeeds
    # 2. k8s_pods_get during verification returns a running healthy pod
    healthy_pod = {
        "status": {
            "phase": "Running",
            "containerStatuses": [{"name": "radarr", "ready": True, "state": {"running": {}}}],
        }
    }
    mcp.call_tool = AsyncMock(
        side_effect=[
            {"status": "deleted"},  # delete
            healthy_pod,  # verify
        ]
    )

    handler = PodCrashLoopHandler(
        mcp_client=mcp,
        approval_manager=_approval_manager(True),
        chat_manager=_chat_manager(),
    )

    state = {
        "event_type": "alert",
        "alert_name": "KubePodCrashLooping",
        "labels": {"namespace": "media", "pod": "radarr-xxx", "container": "radarr"},
        "event_id": "test-ev",
    }

    result = await handler.execute(state, {})
    assert result is not None
    assert result.handled is True
    assert result.is_resolved is True
    assert "radarr-xxx" in result.action_taken


@pytest.mark.asyncio
async def test_execute_aborts_when_operator_denies():
    mcp = MagicMock()
    mcp.call_tool = AsyncMock()
    handler = PodCrashLoopHandler(
        mcp_client=mcp,
        approval_manager=_approval_manager(False),
        chat_manager=_chat_manager(),
    )

    state = {
        "event_type": "alert",
        "alert_name": "KubePodCrashLooping",
        "labels": {"namespace": "media", "pod": "radarr-xxx", "container": "radarr"},
        "event_id": "test-ev",
    }

    result = await handler.execute(state, {})
    assert result is not None
    assert result.handled is True
    assert result.is_resolved is False
    assert result.requires_escalation is True
    # Tool call to delete should not have executed
    assert mcp.call_tool.await_count == 0
