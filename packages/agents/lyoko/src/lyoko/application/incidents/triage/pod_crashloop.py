"""Deterministic triage handler for KubePodCrashLooping alerts."""

import asyncio
import logging
import re
from typing import Any

from langchain_core.runnables import RunnableConfig
from lyoko.application.agents.supervisor import status_callback
from lyoko.application.incidents.triage.base import TriageResult
from lyoko.application.safety.tool_gate import GateMode, make_gate
from lyoko.config import settings
from lyoko.domain.interfaces.approval import ApprovalManagerInterface
from lyoko.domain.interfaces.chat_service import ChatServiceInterface
from lyoko.domain.interfaces.mcp import MCPClientInterface
from lyoko.domain.models.state import is_message

logger = logging.getLogger("lyoko.triage.pod_crashloop")

_DELETE_TOOL = "k8s_pods_delete"
_GET_TOOL = "k8s_pods_get"
_VERIFY_ATTEMPTS = 5

_POD_CRASH_RE = re.compile(
    r"pod\s+([a-zA-Z0-9_\-\.]+)\s+(?:in|namespace)\s+([a-zA-Z0-9_\-]+).*crash",
    re.IGNORECASE,
)


def extract_crashloop_target(state: dict[str, Any]) -> tuple[str, str] | None:
    """Extract (namespace, pod_name) from state if crashlooping is detected."""
    if is_message(state):
        text = state.get("text", "")
        match = _POD_CRASH_RE.search(text)
        if match:
            return match.group(2), match.group(1)
        return None

    alert_name = state.get("alert_name")
    if alert_name in ("KubePodCrashLooping", "homelab-kube-pod-crashlooping"):
        labels = state.get("labels") or {}
        ns = labels.get("namespace", "")
        pod = labels.get("pod", "")
        if ns and pod:
            return ns, pod
    return None


def is_pod_healthy(pod_data: Any) -> bool:
    """Check if the pod is in Running phase and without CrashLoopBackOff."""
    if not isinstance(pod_data, dict):
        return False
    status = pod_data.get("status") or {}
    phase = str(status.get("phase", "")).lower()
    if phase != "running":
        return False
    for c_status in status.get("containerStatuses") or []:
        waiting = (c_status.get("state") or {}).get("waiting") or {}
        if waiting.get("reason") == "CrashLoopBackOff":
            return False
    return True


class PodCrashLoopHandler:
    """Deterministic triage handler for pods in CrashLoopBackOff."""

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
        return extract_crashloop_target(state) is not None

    async def _span(self, name: str, run: Any) -> Any:
        return await self.tracer.traced(name, run) if self.tracer is not None else await run()

    async def execute(self, state: dict[str, Any], config: RunnableConfig) -> TriageResult | None:
        target = extract_crashloop_target(state)
        if not target or self.mcp_client is None:
            return None

        namespace, pod = target
        on_status = status_callback(config)

        async def report(step: str) -> None:
            if on_status is not None:
                try:
                    await on_status(step)
                except Exception:
                    logger.debug("Failed to report status: %s", step)

        plan = f"Delete crashing pod `{pod}` in namespace `{namespace}` to trigger fresh controller recreation."
        root_cause = f"Pod `{pod}` in namespace `{namespace}` is failing in CrashLoopBackOff."

        logger.info(
            "Pod crashloop triage: requesting approval to delete pod %s/%s.", namespace, pod
        )
        await report(f"⏳ Waiting for approval to restart pod {pod} ({namespace})")

        gate = make_gate(
            state,
            GateMode.APPROVAL,
            approval_manager=self.approval_manager,
            chat_manager=self.chat_manager,
            plan=plan,
            mcp_client=self.mcp_client,
        )

        args = {"name": pod, "namespace": namespace}
        refusal = await self._span("operator-approval", lambda: gate.authorize(_DELETE_TOOL, args))
        actions = [record.to_dict() for record in gate.records]

        if refusal is not None:
            # If refused because no approval channel is configured, fall through to diagnose
            if "no approval channel" in refusal.lower():
                logger.info(
                    "Pod crashloop restart: no approval channel configured; falling through to diagnose."
                )
                return None

            logger.info("Pod crashloop restart not approved: %s", refusal)
            return TriageResult(
                handled=True,
                root_cause=root_cause,
                plan=plan,
                action_taken=f"Restart aborted: {refusal}",
                actions=actions,
                requires_escalation=True,
                is_resolved=False,
            )

        await report(f"🛠 Restarting pod {pod}")
        try:
            await self._span(
                f"mcp:{_DELETE_TOOL}",
                lambda: self.mcp_client.call_tool(_DELETE_TOOL, args),
            )
        except Exception as exc:
            logger.error("Failed to delete pod %s: %s", pod, exc, exc_info=True)
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
        for attempt in range(_VERIFY_ATTEMPTS):
            await report(
                f"🔍 Verifying pod health in {namespace} (attempt {attempt + 1}/{_VERIFY_ATTEMPTS})"
            )
            await asyncio.sleep(settings.verification_delay_seconds)
            try:
                pod_data = await self._span(
                    f"mcp:{_GET_TOOL}",
                    lambda: self.mcp_client.call_tool(_GET_TOOL, args),
                )
                if is_pod_healthy(pod_data):
                    resolved = True
                    verification = f"Pod `{pod}` in `{namespace}` is now Running and healthy."
                    break
            except Exception as exc:
                logger.debug("Verification poll error for %s: %s", pod, exc)

        if not resolved:
            verification = f"Pod `{pod}` did not reach healthy Running state within timeout."

        return TriageResult(
            handled=True,
            root_cause=root_cause,
            plan=plan,
            action_taken=f"Deleted pod `{pod}` in namespace `{namespace}` for controller restart.",
            actions=actions,
            requires_escalation=not resolved,
            is_resolved=resolved,
            verification=verification,
        )
