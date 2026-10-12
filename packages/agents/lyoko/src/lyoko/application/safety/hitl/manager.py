import asyncio
import logging

from lyoko.domain.interfaces.approval import ApprovalManagerInterface
from lyoko.domain.models.chat import ApprovalResponse

logger = logging.getLogger("lyoko.hitl")


class ApprovalManager(ApprovalManagerInterface):
    """Manages pending human-in-the-loop (HITL) approval requests."""

    def __init__(self) -> None:
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

    async def wait_for_approval(self, incident_id: str) -> ApprovalResponse:
        """Wait indefinitely for an approval response for the given incident.

        If no pending approval exists for the incident, one will be created.
        """
        fut = self._pending.get(incident_id)
        if fut is None or fut.done():
            fut = self.create_pending_approval(incident_id)

        return await fut
