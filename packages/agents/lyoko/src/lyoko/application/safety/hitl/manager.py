import asyncio
import logging

from lyoko.domain.interfaces.approval import ApprovalManagerInterface
from lyoko.domain.models.chat import ApprovalResponse

logger = logging.getLogger("lyoko.hitl")


class ApprovalManager(ApprovalManagerInterface):
    """Manages pending human-in-the-loop (HITL) approval requests."""

    def __init__(self, default_timeout_seconds: float = 300.0) -> None:
        self.default_timeout_seconds = default_timeout_seconds
        self._pending: dict[str, asyncio.Future[ApprovalResponse]] = {}

    def create_pending_approval(self, incident_id: str) -> asyncio.Future[ApprovalResponse]:
        """Create and register a pending approval future for an incident."""
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[ApprovalResponse] = loop.create_future()
        self._pending[incident_id] = fut
        return fut

    def resolve_approval(self, response: ApprovalResponse) -> bool:
        """Resolve a pending approval future with the received response.

        Returns True if a pending future was found and completed, False otherwise.
        """
        fut = self._pending.pop(response.incident_id, None)
        if fut and not fut.done():
            fut.set_result(response)
            return True
        return False

    async def wait_for_approval(
        self, incident_id: str, timeout: float | None = None
    ) -> ApprovalResponse:
        """Wait for an approval response for the given incident until timeout.

        If no pending approval exists for the incident, one will be created.
        On timeout, an unapproved ApprovalResponse is returned.
        """
        fut = self._pending.get(incident_id)
        if fut is None or fut.done():
            fut = self.create_pending_approval(incident_id)

        timeout_val = timeout if timeout is not None else self.default_timeout_seconds
        if timeout_val and timeout_val > 0:
            try:
                return await asyncio.wait_for(fut, timeout=timeout_val)
            except TimeoutError:
                self._pending.pop(incident_id, None)
                return ApprovalResponse(
                    incident_id=incident_id,
                    approved=False,
                    user_id="system",
                    action_id="timeout",
                    reason=f"Approval timed out after {int(timeout_val)}s",
                )
        return await fut
