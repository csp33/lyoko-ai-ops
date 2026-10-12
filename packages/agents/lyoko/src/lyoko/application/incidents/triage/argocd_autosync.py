"""Deterministic triage handler for Argo CD OutOfSync alerts."""

import asyncio
import logging
from typing import Any

from langchain_core.runnables import RunnableConfig
from lyoko.application.agents.supervisor import status_callback
from lyoko.application.incidents.triage.argocd_inspector import ArgoCDApplicationInspector
from lyoko.application.incidents.triage.base import TriageResult
from lyoko.application.safety.tool_gate import GateMode, make_gate
from lyoko.config import settings
from lyoko.domain.interfaces.approval import ApprovalManagerInterface
from lyoko.domain.interfaces.chat_service import ChatServiceInterface
from lyoko.domain.interfaces.mcp import MCPClientInterface
from lyoko.domain.models.state import is_message

logger = logging.getLogger("lyoko.triage.argocd_autosync")

_VERIFY_ATTEMPTS = 6
_GET_TOOL = "k8s_resources_get"
_APPLY_TOOL = "k8s_resources_create_or_update"
_APPLICATION = {
    "apiVersion": "argoproj.io/v1alpha1",
    "kind": "Application",
    "namespace": "argocd",
}


def _status_line(manifest: dict[str, Any]) -> str:
    status = manifest.get("status") or {}
    sync = (status.get("sync") or {}).get("status", "Unknown")
    health = (status.get("health") or {}).get("status", "Unknown")
    return f"sync={sync} health={health}"


def extract_outofsync_app(state: dict[str, Any]) -> str | None:
    """Extract target Argo CD application name from alert or chat message."""
    if is_message(state):
        return ArgoCDApplicationInspector.parse_outofsync_app(state.get("text", ""))

    alert_name = state.get("alert_name")
    if alert_name in ("ArgoCDAppOutOfSync", "homelab-argocd-app-out-of-sync"):
        labels = state.get("labels") or {}
        return labels.get("name") or labels.get("application") or None
    return None


class ArgoCDAutosyncHandler:
    """Deterministic triage handler for Argo CD OutOfSync with autosync disabled."""

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
        return extract_outofsync_app(state) is not None

    async def _span(self, name: str, run: Any) -> Any:
        return await self.tracer.traced(name, run) if self.tracer is not None else await run()

    async def execute(self, state: dict[str, Any], config: RunnableConfig) -> TriageResult | None:
        app = extract_outofsync_app(state)
        if not app or self.mcp_client is None:
            return None

        on_status = status_callback(config)

        async def report(step: str) -> None:
            if on_status is not None:
                try:
                    await on_status(step)
                except Exception:
                    logger.debug("Failed to report autosync status step '%s'.", step)

        logger.info("Autosync fast-path: checking application '%s'.", app)
        await report(f"🔎 Checking Argo CD autosync for {app}")
        get_args = {**_APPLICATION, "name": app}
        try:
            raw = await self._span(
                f"mcp:{_GET_TOOL}", lambda: self.mcp_client.call_tool(_GET_TOOL, get_args)
            )
            manifest = ArgoCDApplicationInspector.manifest_from_tool_result(raw)
        except Exception as exc:
            logger.warning("Autosync fast-path read failed for %s: %s", app, exc)
            return None

        if (
            manifest is None
            or not ArgoCDApplicationInspector.autosync_disabled(manifest)
            or ArgoCDApplicationInspector.has_sync_error(manifest)
        ):
            logger.info("Autosync fast-path not applicable for '%s'.", app)
            return None

        plan = f"Enable Argo CD autosync for application `{app}` (prune + selfHeal)."
        root_cause = (
            f"The Argo CD application `{app}` is OutOfSync because autosync is disabled "
            "(`spec.syncPolicy.automated`). Without autosync, drift is never reconciled."
        )
        logger.info(
            "Autosync fast-path: '%s' has autosync disabled; requesting operator approval.", app
        )
        await report(f"⏳ Waiting for approval to enable autosync on {app}")
        gate = make_gate(
            state,
            GateMode.APPROVAL,
            approval_manager=self.approval_manager,
            chat_manager=self.chat_manager,
            plan=plan,
            mcp_client=self.mcp_client,
        )
        resource = ArgoCDApplicationInspector.manifest_to_yaml(
            ArgoCDApplicationInspector.enable_autosync(manifest)
        )
        refusal = await self._span(
            "operator-approval",
            lambda: gate.authorize(_APPLY_TOOL, {"resource": resource}),
        )
        actions = [record.to_dict() for record in gate.records]

        if refusal is not None:
            logger.info("Autosync fast-path: '%s' not approved: %s", app, refusal)
            return TriageResult(
                handled=True,
                root_cause=root_cause,
                plan=plan,
                action_taken=f"Remediation aborted: {refusal}",
                actions=actions,
                requires_escalation=True,
                is_resolved=False,
            )

        await report(f"🛠 Enabling autosync on {app}")
        try:
            await self._span(
                f"mcp:{_APPLY_TOOL}",
                lambda: self.mcp_client.call_tool(_APPLY_TOOL, {"resource": resource}),
            )
        except Exception as exc:
            logger.error("Autosync fast-path apply failed for %s: %s", app, exc, exc_info=True)
            return TriageResult(
                handled=True,
                root_cause=root_cause,
                plan=plan,
                action_taken=f"Remediation failed: {exc}",
                actions=actions,
                requires_escalation=True,
                is_resolved=False,
            )

        verification = ""
        resolved = False
        for attempt in range(_VERIFY_ATTEMPTS):
            await report(
                f"🔍 Verifying {app} is Synced and Healthy"
                + (f" ({attempt}/{_VERIFY_ATTEMPTS - 1})" if attempt else "")
            )
            await asyncio.sleep(settings.verification_delay_seconds)
            try:
                raw_after = await self._span(
                    f"mcp:{_GET_TOOL}", lambda: self.mcp_client.call_tool(_GET_TOOL, get_args)
                )
                after = ArgoCDApplicationInspector.manifest_from_tool_result(raw_after)
            except Exception as exc:
                logger.warning("Autosync fast-path verification failed for %s: %s", app, exc)
                break
            if after is None:
                break
            resolved = ArgoCDApplicationInspector.is_synced_and_healthy(after)
            verification = f"Application `{app}` now {_status_line(after)}."
            if resolved:
                break

        logger.info("Autosync fast-path: '%s' resolved=%s (%s).", app, resolved, verification)
        return TriageResult(
            handled=True,
            root_cause=root_cause,
            plan=plan,
            action_taken=f"Enabled Argo CD autosync for `{app}` (prune + selfHeal).",
            actions=actions,
            requires_escalation=not resolved,
            is_resolved=resolved,
            verification=verification,
        )
