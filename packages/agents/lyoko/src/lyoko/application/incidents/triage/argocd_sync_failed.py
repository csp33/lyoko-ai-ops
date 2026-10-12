"""Deterministic triage handler for ArgoCDAppSyncFailed alerts."""

import logging
import re
from typing import Any

import yaml
from langchain_core.runnables import RunnableConfig
from lyoko.application.agents.supervisor import status_callback
from lyoko.application.incidents.triage.base import TriageResult
from lyoko.domain.interfaces.approval import ApprovalManagerInterface
from lyoko.domain.interfaces.chat_service import ChatServiceInterface
from lyoko.domain.interfaces.mcp import MCPClientInterface
from lyoko.domain.models.state import is_message

logger = logging.getLogger("lyoko.triage.argocd_sync_failed")

_GET_TOOL = "k8s_resources_get"
_ARGOCD_NAMESPACE = "argocd"

_APP_SYNC_RE = re.compile(
    r"(?:argo\s*cd\s+app(?:lication)?)\s+([a-zA-Z0-9_\-\.]+).*sync\s+failed",
    re.IGNORECASE,
)


def extract_sync_failed_app(state: dict[str, Any]) -> str | None:
    """Extract Argo CD application name from state."""
    if is_message(state):
        text = state.get("text", "")
        match = _APP_SYNC_RE.search(text)
        if match:
            return match.group(1).strip("`'\".,:;")
        return None

    alert_name = state.get("alert_name") or ""
    if alert_name in ("ArgoCDAppSyncFailed", "homelab-argocd-app-sync-failed"):
        labels = state.get("labels") or {}
        return labels.get("name") or labels.get("application") or None
    return None


def _parse_manifest(result: Any) -> dict[str, Any] | None:
    content = result.get("content", result) if isinstance(result, dict) else result
    if isinstance(content, dict):
        return content
    if not isinstance(content, str):
        return None
    try:
        loaded = yaml.safe_load(content)
        return loaded if isinstance(loaded, dict) else None
    except Exception:
        return None


def is_permanent_manifest_error(manifest: dict[str, Any]) -> bool:
    """Check if the failure is due to invalid manifests, git errors, or schema errors."""
    status = manifest.get("status") or {}
    operation = status.get("operationState") or {}
    message = str(operation.get("message") or "").lower()

    if any(
        err in message
        for err in (
            "comparisonerror",
            "failed to parse",
            "yaml:",
            "schema validation error",
            "unknown field",
        )
    ):
        return True

    conditions = status.get("conditions") or []
    for cond in conditions:
        if isinstance(cond, dict):
            c_type = str(cond.get("type", "")).lower()
            if c_type in ("comparisonerror", "invalidspecerror"):
                return True
    return False


class ArgoCDSyncFailedHandler:
    """Deterministic triage handler for Argo CD sync failures."""

    def __init__(
        self,
        mcp_client: MCPClientInterface | None,
        approval_manager: ApprovalManagerInterface | None = None,
        chat_manager: ChatServiceInterface | None = None,
        tracer: Any = None,
    ) -> None:
        self.mcp_client = mcp_client
        self.approval_manager = approval_manager
        self.chat_manager = chat_manager
        self.tracer = tracer

    def can_handle(self, state: dict[str, Any]) -> bool:
        if self.mcp_client is None:
            return False
        return extract_sync_failed_app(state) is not None

    async def _span(self, name: str, run: Any) -> Any:
        return await self.tracer.traced(name, run) if self.tracer is not None else await run()

    async def execute(self, state: dict[str, Any], config: RunnableConfig) -> TriageResult | None:
        app = extract_sync_failed_app(state)
        if not app or self.mcp_client is None:
            return None

        on_status = status_callback(config)

        async def report(step: str) -> None:
            if on_status is not None:
                try:
                    await on_status(step)
                except Exception:
                    logger.debug("Failed to report status: %s", step)

        await report(f"🔎 Inspecting Argo CD application {app} status")
        get_args = {
            "apiVersion": "argoproj.io/v1alpha1",
            "kind": "Application",
            "name": app,
            "namespace": _ARGOCD_NAMESPACE,
        }

        try:
            raw = await self._span(
                f"mcp:{_GET_TOOL}",
                lambda: self.mcp_client.call_tool(_GET_TOOL, get_args),
            )
            manifest = _parse_manifest(raw)
        except Exception as exc:
            logger.warning("Failed to fetch Argo CD app %s: %s", app, exc)
            return None

        if not manifest:
            return None

        status = manifest.get("status") or {}
        operation = status.get("operationState") or {}
        op_phase = str(operation.get("phase", "")).lower()
        sync_status = str((status.get("sync") or {}).get("status", "")).lower()

        # If already Succeeded and Synced, alert is stale/recovered
        if op_phase == "succeeded" and sync_status == "synced":
            logger.info("Application %s is already Succeeded and Synced.", app)
            return TriageResult(
                handled=True,
                root_cause=f"Argo CD application `{app}` operation previously failed but has already succeeded.",
                plan="None required. Application is already synced and healthy.",
                action_taken="No action taken (transient sync failure already cleared).",
                is_resolved=True,
                verification="Application operationState is Succeeded and sync status is Synced.",
            )

        # If permanent manifest or comparison error, fall through to LLM diagnose
        if is_permanent_manifest_error(manifest):
            logger.info(
                "Application %s has permanent manifest/comparison error; delegating to diagnose.",
                app,
            )
            return None

        # If it's a known transient lock/conflict error
        op_msg = str(operation.get("message") or "")
        return TriageResult(
            handled=True,
            root_cause=f"Argo CD sync for `{app}` failed with transient message: {op_msg}",
            plan=f"Inspect Argo CD application `{app}` logs and retry sync.",
            action_taken="Identified transient sync error; escalated for operator review.",
            requires_escalation=True,
            is_resolved=False,
            verification=f"Operation phase is currently {op_phase}.",
        )
