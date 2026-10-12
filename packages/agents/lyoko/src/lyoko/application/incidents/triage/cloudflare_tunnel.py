"""Deterministic triage handler for Cloudflare Tunnel pod restarts and not-ready alerts."""

import asyncio
import logging
from typing import Any

import yaml
from langchain_core.runnables import RunnableConfig
from lyoko.application.agents.supervisor import status_callback
from lyoko.application.incidents.triage.base import TriageResult
from lyoko.application.safety.tool_gate import GateMode, make_gate
from lyoko.config import settings
from lyoko.domain.interfaces.approval import ApprovalManagerInterface
from lyoko.domain.interfaces.chat_service import ChatServiceInterface
from lyoko.domain.interfaces.mcp import MCPClientInterface
from lyoko.domain.models.state import is_message

logger = logging.getLogger("lyoko.triage.cloudflare_tunnel")

_NAMESPACE = "cloudflare-tunnel"
_DEPLOYMENT = "cloudflare-tunnel"
_LIST_PODS_TOOL = "k8s_pods_list_in_namespace"
_DELETE_POD_TOOL = "k8s_pods_delete"
_GET_RESOURCE_TOOL = "k8s_resources_get"
_VERIFY_ATTEMPTS = 5

_CFT_ALERTS = {
    "CloudflaredPodNotReady",
    "CloudflaredPodRestarts",
    "cft-pod-not-ready",
    "cft-pod-restarts",
}


def is_cloudflare_tunnel_event(state: dict[str, Any]) -> bool:
    """Return True if state corresponds to a Cloudflare Tunnel disruption."""
    if is_message(state):
        text = (state.get("text") or "").lower()
        return "cloudflare" in text or "cloudflared" in text

    alert_name = state.get("alert_name") or ""
    if alert_name in _CFT_ALERTS:
        return True

    labels = state.get("labels") or {}
    ns = labels.get("namespace", "")
    app = labels.get("app", "")
    deployment = labels.get("deployment", "")
    return ns == _NAMESPACE and (app == _DEPLOYMENT or deployment == _DEPLOYMENT)


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


def is_deployment_available(manifest: dict[str, Any] | None) -> bool:
    """Check if deployment reports availableReplicas >= 1."""
    if not manifest:
        return False
    status = manifest.get("status") or {}
    available = status.get("availableReplicas", 0)
    ready = status.get("readyReplicas", 0)
    return int(available) >= 1 or int(ready) >= 1


class CloudflareTunnelHandler:
    """Deterministic triage handler restarting cloudflared pod on disruption."""

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
        return is_cloudflare_tunnel_event(state)

    async def _span(self, name: str, run: Any) -> Any:
        return await self.tracer.traced(name, run) if self.tracer is not None else await run()

    async def _find_target_pod(self) -> str | None:
        assert self.mcp_client is not None
        try:
            raw = await self._span(
                f"mcp:{_LIST_PODS_TOOL}",
                lambda: self.mcp_client.call_tool(_LIST_PODS_TOOL, {"namespace": _NAMESPACE}),
            )
            manifest = _parse_manifest(raw)
            if not manifest:
                return None
            items = manifest.get("items") or []
            for item in items:
                name = (item.get("metadata") or {}).get("name", "")
                if name.startswith(_DEPLOYMENT):
                    return name
        except Exception as exc:
            logger.warning("Failed to list pods in %s: %s", _NAMESPACE, exc)
        return None

    async def execute(self, state: dict[str, Any], config: RunnableConfig) -> TriageResult | None:
        if self.mcp_client is None:
            return None

        on_status = status_callback(config)

        async def report(step: str) -> None:
            if on_status is not None:
                try:
                    await on_status(step)
                except Exception:
                    logger.debug("Failed to report status: %s", step)

        await report("🔎 Inspecting Cloudflare Tunnel deployment")
        pod_name = await self._find_target_pod()
        if not pod_name:
            logger.info("No cloudflared pod found in %s; falling through.", _NAMESPACE)
            return None

        plan = (
            f"Restart Cloudflare Tunnel by deleting pod `{pod_name}` in namespace `{_NAMESPACE}`."
        )
        root_cause = "Cloudflare Tunnel pod is not ready or continuously restarting."

        logger.info("Cloudflare tunnel triage: requesting approval to restart %s.", pod_name)
        await report(f"⏳ Waiting for approval to restart Cloudflare Tunnel ({pod_name})")

        gate = make_gate(
            state,
            GateMode.APPROVAL,
            approval_manager=self.approval_manager,
            chat_manager=self.chat_manager,
            plan=plan,
            mcp_client=self.mcp_client,
        )

        args = {"name": pod_name, "namespace": _NAMESPACE}
        refusal = await self._span(
            "operator-approval", lambda: gate.authorize(_DELETE_POD_TOOL, args)
        )
        actions = [record.to_dict() for record in gate.records]

        if refusal is not None:
            if "no approval channel" in refusal.lower():
                logger.info(
                    "Cloudflare tunnel restart: no approval channel configured; falling through to diagnose."
                )
                return None

            logger.info("Cloudflare tunnel restart not approved: %s", refusal)
            return TriageResult(
                handled=True,
                root_cause=root_cause,
                plan=plan,
                action_taken=f"Restart aborted: {refusal}",
                actions=actions,
                requires_escalation=True,
                is_resolved=False,
            )

        await report(f"🛠 Deleting pod {pod_name} to trigger restart")
        try:
            await self._span(
                f"mcp:{_DELETE_POD_TOOL}",
                lambda: self.mcp_client.call_tool(_DELETE_POD_TOOL, args),
            )
        except Exception as exc:
            logger.error("Failed to delete cloudflared pod %s: %s", pod_name, exc, exc_info=True)
            return TriageResult(
                handled=True,
                root_cause=root_cause,
                plan=plan,
                action_taken=f"Restart failed: {exc}",
                actions=actions,
                requires_escalation=True,
                is_resolved=False,
            )

        resolved = False
        verification = ""
        get_deploy_args = {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "name": _DEPLOYMENT,
            "namespace": _NAMESPACE,
        }

        for attempt in range(_VERIFY_ATTEMPTS):
            await report(
                f"🔍 Verifying Cloudflare Tunnel replica readiness (attempt {attempt + 1}/{_VERIFY_ATTEMPTS})"
            )
            await asyncio.sleep(settings.verification_delay_seconds)
            try:
                raw_dep = await self._span(
                    f"mcp:{_GET_RESOURCE_TOOL}",
                    lambda: self.mcp_client.call_tool(_GET_RESOURCE_TOOL, get_deploy_args),
                )
                dep_manifest = _parse_manifest(raw_dep)
                if is_deployment_available(dep_manifest):
                    resolved = True
                    verification = "Cloudflare Tunnel deployment has at least 1 available replica."
                    break
            except Exception as exc:
                logger.debug("Verification poll error for %s: %s", _DEPLOYMENT, exc)

        if not resolved:
            verification = "Cloudflare Tunnel deployment did not become ready within timeout."

        return TriageResult(
            handled=True,
            root_cause=root_cause,
            plan=plan,
            action_taken=f"Restarted Cloudflare Tunnel pod `{pod_name}` in `{_NAMESPACE}`.",
            actions=actions,
            requires_escalation=not resolved,
            is_resolved=resolved,
            verification=verification,
        )
