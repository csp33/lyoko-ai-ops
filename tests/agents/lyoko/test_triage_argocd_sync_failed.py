"""Tests for deterministic ArgoCDAppSyncFailed triage handler."""

from unittest.mock import AsyncMock, MagicMock

import pytest
import yaml
from lyoko.application.incidents.triage.argocd_sync_failed import ArgoCDSyncFailedHandler


def _app_manifest(phase: str, sync_status: str, msg: str = "") -> dict:
    return {
        "apiVersion": "argoproj.io/v1alpha1",
        "kind": "Application",
        "metadata": {"name": "media-app", "namespace": "argocd"},
        "status": {
            "sync": {"status": sync_status},
            "health": {"status": "Healthy"},
            "operationState": {
                "phase": phase,
                "message": msg,
            },
        },
    }


def test_can_handle_detects_alert_and_message():
    handler = ArgoCDSyncFailedHandler(mcp_client=MagicMock())

    alert_state = {
        "event_type": "alert",
        "alert_name": "ArgoCDAppSyncFailed",
        "labels": {"name": "media-app"},
    }
    assert handler.can_handle(alert_state) is True

    msg_state = {
        "event_type": "message",
        "text": "Argo CD app media-app sync failed",
    }
    assert handler.can_handle(msg_state) is True

    other = {
        "event_type": "alert",
        "alert_name": "KubePodOOMKilled",
    }
    assert handler.can_handle(other) is False


@pytest.mark.asyncio
async def test_execute_resolves_when_already_succeeded():
    mcp = MagicMock()
    mcp.call_tool = AsyncMock(return_value=yaml.safe_dump(_app_manifest("Succeeded", "Synced")))
    handler = ArgoCDSyncFailedHandler(mcp_client=mcp)

    state = {
        "event_type": "alert",
        "alert_name": "ArgoCDAppSyncFailed",
        "labels": {"name": "media-app"},
    }

    result = await handler.execute(state, {})
    assert result is not None
    assert result.handled is True
    assert result.is_resolved is True
    assert "succeeded" in result.verification.lower()
    assert "synced" in result.verification.lower()


@pytest.mark.asyncio
async def test_execute_falls_through_on_manifest_comparison_error():
    mcp = MagicMock()
    mcp.call_tool = AsyncMock(
        return_value=yaml.safe_dump(
            _app_manifest("Failed", "OutOfSync", "ComparisonError: invalid yaml in values.yaml")
        )
    )
    handler = ArgoCDSyncFailedHandler(mcp_client=mcp)

    state = {
        "event_type": "alert",
        "alert_name": "ArgoCDAppSyncFailed",
        "labels": {"name": "media-app"},
    }

    # Should fall through (return None) so diagnose LLM can inspect git/manifest errors
    result = await handler.execute(state, {})
    assert result is None
