import asyncio

import pytest
from lyoko.application.safety.hitl.manager import ApprovalManager
from lyoko.domain.models.chat import ApprovalResponse


@pytest.mark.asyncio
async def test_approval_manager_resolve_pending():
    mgr = ApprovalManager()
    fut = mgr.create_pending_approval("inc-123")

    # Resolve
    resolved = mgr.resolve_approval(
        ApprovalResponse(incident_id="inc-123", approved=True, user_id="user1")
    )
    assert resolved is True

    res = await fut
    assert res.approved is True
    assert res.user_id == "user1"


@pytest.mark.asyncio
async def test_approval_manager_wait_indefinite_resolved():
    mgr = ApprovalManager()

    async def _resolve_soon():
        await asyncio.sleep(0.05)
        mgr.resolve_approval(
            ApprovalResponse(
                incident_id="inc-789",
                approved=True,
                user_id="operator",
                action_id="approve",
                reason="Looks safe",
            )
        )

    task = asyncio.create_task(_resolve_soon())
    res = await mgr.wait_for_approval("inc-789")
    await task

    assert res.approved is True
    assert res.user_id == "operator"
    assert res.action_id == "approve"
    assert res.reason == "Looks safe"


@pytest.mark.asyncio
async def test_approval_manager_resolve_nonexistent_or_done():
    mgr = ApprovalManager()
    # Nonexistent
    assert (
        mgr.resolve_approval(ApprovalResponse(incident_id="unknown", approved=True, user_id="u1"))
        is False
    )

    # Already resolved
    fut = mgr.create_pending_approval("inc-done")
    fut.set_result(ApprovalResponse(incident_id="inc-done", approved=False, user_id="u1"))
    # The fut is in _pending and already done
    assert (
        mgr.resolve_approval(ApprovalResponse(incident_id="inc-done", approved=True, user_id="u1"))
        is False
    )
