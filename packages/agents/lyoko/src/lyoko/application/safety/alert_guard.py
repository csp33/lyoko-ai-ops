"""Alert Storm Protector and cascade failure deduplication service.

Prevents trace spam and token cost explosions during cascading infrastructure failures by:
1. Debouncing and grouping bursts of firing alerts into a single consolidated Incident.
2. Deduplicating in-flight investigations and suppressing flapping alerts via cooldown TTL.
3. Providing an emergency Circuit Breaker when alert velocity exceeds storm thresholds.
4. Enabling manual operator override ("Procesar de todas formas") via interactive Telegram buttons.
5. Throttling concurrent LangGraph runs using an asyncio Semaphore.
"""

import asyncio
import logging
import time
from collections import deque
from collections.abc import Awaitable, Callable
from enum import StrEnum
from typing import Any

from lyoko.application.safety.storm_approval import AlertStormApprovalFactory
from lyoko.config import AgentSettings
from lyoko.config import settings as default_settings
from lyoko.domain.models.chat import ApprovalResponse
from lyoko.domain.models.incident import Incident, compute_incident_key

logger = logging.getLogger("lyoko.alert_guard")


class CircuitState(StrEnum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class AlertStormProtector:
    """Guards LYOKO against alert storms, cascading trace spam, and flapping loops."""

    def __init__(
        self,
        settings: AgentSettings | None = None,
        chat_manager: Any = None,
        dispatch_callback: Callable[[Incident], Awaitable[None]] | None = None,
        task_tracker: Any = None,
    ) -> None:
        self._settings = settings or default_settings
        self._chat_manager = chat_manager
        self._dispatch_callback = dispatch_callback
        self._task_tracker = task_tracker

        self._circuit_state = CircuitState.CLOSED
        self._circuit_tripped_at: float = 0.0
        self._recent_alert_timestamps: deque[float] = deque()

        self._in_flight_keys: set[str] = set()
        self._cooldown_cache: dict[str, float] = {}
        self._suppressed_incidents: dict[str, Incident] = {}

        self._pending_alerts: list[dict[str, Any]] = []
        self._debounce_task: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()
        self._semaphore = asyncio.Semaphore(self._settings.max_concurrent_incidents)

    @property
    def circuit_state(self) -> CircuitState:
        now = time.monotonic()
        if (
            self._circuit_state == CircuitState.OPEN
            and now - self._circuit_tripped_at >= self._settings.alert_storm_cooldown_seconds
        ):
            self._circuit_state = CircuitState.HALF_OPEN
            logger.info("Alert storm circuit breaker transitioned to HALF_OPEN")
        return self._circuit_state

    def get_incident_key(self, incident: Incident) -> str:
        return compute_incident_key(incident)

    def is_in_flight(self, key: str) -> bool:
        return key in self._in_flight_keys

    def is_in_cooldown(self, key: str) -> bool:
        now = time.monotonic()
        expiry = self._cooldown_cache.get(key)
        if expiry is None:
            return False
        if now >= expiry:
            del self._cooldown_cache[key]
            return False
        return True

    def mark_in_flight(self, key: str) -> None:
        self._in_flight_keys.add(key)

    def mark_completed(self, key: str) -> None:
        self._in_flight_keys.discard(key)
        if self._settings.alert_dedup_cooldown_seconds > 0:
            self._cooldown_cache[key] = (
                time.monotonic() + self._settings.alert_dedup_cooldown_seconds
            )

    async def ingest_alerts(self, raw_alerts: list[dict[str, Any]]) -> None:
        firing = [a for a in raw_alerts if a.get("status") == "firing"]
        if not firing:
            return

        now = time.monotonic()
        async with self._lock:
            win_start = now - self._settings.alert_storm_window_seconds
            while self._recent_alert_timestamps and self._recent_alert_timestamps[0] < win_start:
                self._recent_alert_timestamps.popleft()

            for _ in firing:
                self._recent_alert_timestamps.append(now)

            alert_count = len(self._recent_alert_timestamps)
            if (
                alert_count > self._settings.alert_storm_threshold
                and self.circuit_state != CircuitState.OPEN
            ):
                self._circuit_state = CircuitState.OPEN
                self._circuit_tripped_at = now
                logger.warning("Alert storm (%d alerts)! Tripping breaker to OPEN.", alert_count)
                await self._notify_storm(firing, alert_count)
                self._pending_alerts.clear()
                if self._debounce_task and not self._debounce_task.done():
                    self._debounce_task.cancel()
                return

            if self.circuit_state == CircuitState.OPEN:
                logger.warning("Circuit breaker OPEN. Dropping %d alerts.", len(firing))
                return

            if self._circuit_state == CircuitState.HALF_OPEN:
                self._circuit_state = CircuitState.CLOSED

            filtered: list[dict[str, Any]] = []
            for alert in firing:
                labels = {str(k): str(v) for k, v in (alert.get("labels") or {}).items()}
                candidate = Incident(
                    alert_name=labels.get("alertname", "UnknownAlert"),
                    namespace=labels.get("namespace", ""),
                    pod_name=labels.get("pod", ""),
                    deployment_name=labels.get("deployment") or labels.get("app"),
                    fingerprint=alert.get("fingerprint"),
                    labels=labels,
                    annotations={
                        str(k): str(v) for k, v in (alert.get("annotations") or {}).items()
                    },
                )
                key = self.get_incident_key(candidate)
                if self.is_in_flight(key):
                    continue
                if self.is_in_cooldown(key):
                    self._suppressed_incidents[key] = candidate
                    await self._notify_suppressed(candidate, key)
                    continue
                filtered.append(alert)

            if not filtered:
                return

            self._pending_alerts.extend(filtered)
            if self._settings.alert_debounce_seconds <= 0:
                await self._flush_pending_locked()
            elif self._debounce_task is None or self._debounce_task.done():
                self._debounce_task = asyncio.create_task(self._debounce_timer())

    async def _debounce_timer(self) -> None:
        try:
            await asyncio.sleep(self._settings.alert_debounce_seconds)
            async with self._lock:
                await self._flush_pending_locked()
        except asyncio.CancelledError:
            pass

    async def _flush_pending_locked(self) -> None:
        if not self._pending_alerts:
            return
        grouped: dict[str, list[dict[str, Any]]] = {}
        for a in self._pending_alerts:
            grouped.setdefault(a.get("labels", {}).get("namespace", ""), []).append(a)
        self._pending_alerts.clear()

        for _ns, alerts in grouped.items():
            primary, corrs = alerts[0], alerts[1:]
            p_labels = {str(k): str(v) for k, v in (primary.get("labels") or {}).items()}
            incident = Incident(
                alert_name=p_labels.get("alertname", "UnknownAlert"),
                namespace=p_labels.get("namespace", ""),
                pod_name=p_labels.get("pod", ""),
                deployment_name=p_labels.get("deployment") or p_labels.get("app"),
                fingerprint=primary.get("fingerprint"),
                labels=p_labels,
                annotations={str(k): str(v) for k, v in (primary.get("annotations") or {}).items()},
                correlated_alerts=[
                    {
                        "alertname": c.get("labels", {}).get("alertname", "UnknownAlert"),
                        "namespace": c.get("labels", {}).get("namespace", ""),
                        "pod": c.get("labels", {}).get("pod", ""),
                        "labels": c.get("labels", {}),
                        "annotations": c.get("annotations", {}),
                    }
                    for c in corrs
                ],
            )
            key = self.get_incident_key(incident)
            self.mark_in_flight(key)
            if self._dispatch_callback:
                if self._settings.alert_debounce_seconds <= 0:
                    await self._dispatch_with_semaphore(incident, key)
                else:
                    self._track_task(self._dispatch_with_semaphore(incident, key))

    def _track_task(self, coro: Awaitable[Any]) -> asyncio.Task[Any]:
        task = asyncio.create_task(coro)
        if self._task_tracker is not None:
            self._task_tracker.track(task)
        return task

    async def _dispatch_with_semaphore(self, incident: Incident, key: str) -> None:
        try:
            async with self._semaphore:
                if self._dispatch_callback:
                    await self._dispatch_callback(incident)
        finally:
            self.mark_completed(key)

    async def handle_force_approval(self, response: ApprovalResponse) -> bool:
        """Handle operator clicking 'Procesar de todas formas' button."""
        if response.action_id != "force":
            return False
        incident = self._suppressed_incidents.pop(response.incident_id, None)
        if incident is None:
            return False
        key = self.get_incident_key(incident)
        self._cooldown_cache.pop(key, None)
        self.mark_in_flight(key)
        if self._dispatch_callback:
            self._track_task(self._dispatch_with_semaphore(incident, key))
        return True

    async def _notify_storm(self, alerts: list[dict[str, Any]], count: int) -> None:
        if not self._chat_manager:
            return
        storm_key = f"storm-{int(time.time())}"
        storm_inc, req = AlertStormApprovalFactory.build_storm_approval_request(
            storm_key=storm_key,
            alerts=alerts,
            count=count,
            window_seconds=self._settings.alert_storm_window_seconds,
            cooldown_seconds=self._settings.alert_storm_cooldown_seconds,
            default_chat_id=self._settings.telegram_default_chat_id or "",
        )
        self._suppressed_incidents[storm_key] = storm_inc
        try:
            if hasattr(self._chat_manager, "broadcast_approval_request"):
                await self._chat_manager.broadcast_approval_request(req)
            elif hasattr(self._chat_manager, "broadcast_message"):
                await self._chat_manager.broadcast_message(
                    chat_id=req.chat_id, text=f"{req.title}\n\n{req.details}"
                )
        except Exception:
            logger.exception("Failed to send storm notification")

    async def _notify_suppressed(self, incident: Incident, key: str) -> None:
        if not self._chat_manager:
            return
        req = AlertStormApprovalFactory.build_suppressed_approval_request(
            key=key,
            incident=incident,
            default_chat_id=self._settings.telegram_default_chat_id or "",
        )
        try:
            if hasattr(self._chat_manager, "broadcast_approval_request"):
                await self._chat_manager.broadcast_approval_request(req)
        except Exception:
            logger.exception("Failed to send suppressed notification")
