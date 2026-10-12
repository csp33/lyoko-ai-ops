"""Unit tests for TaskTracker in-flight task tracking and graceful draining."""

import asyncio

import pytest
from lyoko.application.safety.task_tracker import TaskTracker


@pytest.mark.asyncio
async def test_task_tracker_track_and_auto_discard():
    """Tracked tasks are auto-discarded when they finish."""
    tracker = TaskTracker()
    assert tracker.active_count == 0

    async def sample_task():
        await asyncio.sleep(0.01)

    task = asyncio.create_task(sample_task())
    tracker.track(task)
    assert tracker.active_count == 1

    await task
    # Give done callback a cycle
    await asyncio.sleep(0.01)
    assert tracker.active_count == 0


@pytest.mark.asyncio
async def test_task_tracker_drain_completes_in_flight():
    """Drain awaits all in-flight tasks until they finish."""
    tracker = TaskTracker()

    task_finished = False

    async def in_flight():
        nonlocal task_finished
        await asyncio.sleep(0.05)
        task_finished = True

    task = asyncio.create_task(in_flight())
    tracker.track(task)

    await tracker.drain(timeout=0.2)
    assert task_finished is True
    assert tracker.is_closing is True
    assert tracker.active_count == 0


@pytest.mark.asyncio
async def test_task_tracker_drain_timeout_cancels_pending():
    """Tasks that do not complete within timeout are cancelled."""
    tracker = TaskTracker()

    async def hung_task():
        try:
            await asyncio.sleep(100.0)
        except asyncio.CancelledError:
            raise

    task = asyncio.create_task(hung_task())
    tracker.track(task)

    await tracker.drain(timeout=0.05)
    assert task.cancelled()
    assert tracker.is_closing is True
