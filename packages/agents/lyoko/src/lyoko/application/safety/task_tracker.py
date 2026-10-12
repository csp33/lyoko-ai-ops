"""In-flight task tracking and graceful draining coordinator."""

import asyncio
import logging
from typing import Any

logger = logging.getLogger("lyoko.task_tracker")


class TaskTracker:
    """Tracks running asyncio Tasks to ensure graceful draining on shutdown."""

    def __init__(self) -> None:
        self._tasks: set[asyncio.Task[Any]] = set()
        self._closing: bool = False

    @property
    def is_closing(self) -> bool:
        """Check if shutdown draining has commenced."""
        return self._closing

    @property
    def active_count(self) -> int:
        """Return the count of currently running tracked tasks."""
        return len(self._tasks)

    def track(self, task: asyncio.Task[Any]) -> asyncio.Task[Any]:
        """Register a task to be tracked during execution."""
        if self._closing:
            logger.warning(
                "TaskTracker is draining; task %s will still be tracked.", task.get_name()
            )
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def drain(self, timeout: float = 25.0) -> None:
        """Wait for in-flight tasks to complete up to timeout seconds."""
        self._closing = True
        active_tasks = [t for t in self._tasks if not t.done()]
        if not active_tasks:
            logger.debug("No active tasks to drain.")
            return

        logger.info("Draining %d active tasks (timeout=%.1fs)...", len(active_tasks), timeout)
        done, pending = await asyncio.wait(active_tasks, timeout=timeout)
        logger.info(
            "Drained %d tasks (%d tasks still pending upon timeout)", len(done), len(pending)
        )
        for task in pending:
            logger.warning("Cancelling task %s after shutdown drain timeout.", task.get_name())
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
