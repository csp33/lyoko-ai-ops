"""Abstract interface for human-in-the-loop (HITL) approval management."""

import asyncio
from abc import ABC, abstractmethod

from lyoko.domain.models.chat import ApprovalResponse


class ApprovalManagerInterface(ABC):
    """Manages pending human-in-the-loop (HITL) approval requests."""

    @abstractmethod
    def create_pending_approval(self, incident_id: str) -> asyncio.Future[ApprovalResponse]:
        """Create and register a pending approval future for an incident."""

    @abstractmethod
    def resolve_approval(self, response: ApprovalResponse) -> bool:
        """Resolve a pending approval future with the received response.

        Returns True if a pending future was found and completed, False otherwise.
        """

    @abstractmethod
    async def wait_for_approval(
        self, incident_id: str, timeout: float | None = None
    ) -> ApprovalResponse:
        """Wait for an approval response for the given incident until timeout.

        If timeout is provided and expires, an unapproved ApprovalResponse with
        action_id='timeout' is returned.
        """
