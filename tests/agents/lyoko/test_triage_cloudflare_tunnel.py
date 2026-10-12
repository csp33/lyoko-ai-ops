"""Tests for deterministic Cloudflare Tunnel pod triage handler."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from lyoko.application.incidents.triage.cloudflare_tunnel import CloudflareTunnelHandler


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


def test_can_handle_detects_alerts_and_messages():
    handler = CloudflareTunnelHandler(mcp_client=MagicMock())

    alert_not_ready = {
        "event_type": "alert",
        "alert_name": "CloudflaredPodNotReady",
        "labels": {"namespace": "cloudflare-tunnel", "deployment": "cloudflare-tunnel"},
    }
    assert handler.can_handle(alert_not_ready) is True

    alert_restarts = {
        "event_type": "alert",
        "alert_name": "CloudflaredPodRestarts",
        "labels": {"namespace": "cloudflare-tunnel", "app": "cloudflare-tunnel"},
    }
    assert handler.can_handle(alert_restarts) is True

    msg = {
        "event_type": "message",
        "text": "cloudflared pod is not ready in cloudflare-tunnel",
    }
    assert handler.can_handle(msg) is True

    unrelated = {
        "event_type": "alert",
        "alert_name": "NodeCpuTemperatureHigh",
    }
    assert handler.can_handle(unrelated) is False


@pytest.mark.asyncio
async def test_execute_restarts_cloudflared_with_approval(monkeypatch):
    from lyoko.config import settings

    monkeypatch.setattr(settings, "verification_delay_seconds", 0)

    mcp = MagicMock()
    # 1. k8s_pods_list_in_namespace -> finds cloudflare-tunnel-xxx
    # 2. k8s_pods_delete -> deletes pod
    # 3. k8s_resources_get -> deployment status availableReplicas >= 1
    deployment_ok = {"status": {"availableReplicas": 1, "replicas": 1, "readyReplicas": 1}}
    mcp.call_tool = AsyncMock(
        side_effect=[
            {"items": [{"metadata": {"name": "cloudflare-tunnel-6f8b9d-abc"}}]},
            {"status": "deleted"},
            deployment_ok,
        ]
    )

    handler = CloudflareTunnelHandler(
        mcp_client=mcp,
        approval_manager=_approval_manager(True),
        chat_manager=_chat_manager(),
    )

    state = {
        "event_type": "alert",
        "alert_name": "CloudflaredPodNotReady",
        "labels": {"namespace": "cloudflare-tunnel"},
        "event_id": "cft-ev",
    }

    result = await handler.execute(state, {})
    assert result is not None
    assert result.handled is True
    assert result.is_resolved is True
    assert "cloudflare-tunnel" in result.action_taken


@pytest.mark.asyncio
async def test_execute_aborts_when_operator_denies():
    mcp = MagicMock()
    mcp.call_tool = AsyncMock(
        return_value={"items": [{"metadata": {"name": "cloudflare-tunnel-6f8b9d-abc"}}]}
    )
    handler = CloudflareTunnelHandler(
        mcp_client=mcp,
        approval_manager=_approval_manager(False),
        chat_manager=_chat_manager(),
    )

    state = {
        "event_type": "alert",
        "alert_name": "CloudflaredPodNotReady",
        "labels": {"namespace": "cloudflare-tunnel"},
        "event_id": "cft-ev",
    }

    result = await handler.execute(state, {})
    assert result is not None
    assert result.handled is True
    assert result.is_resolved is False
    assert result.requires_escalation is True
