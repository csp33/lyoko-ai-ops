"""Orphaned incident workflow recovery coordinator."""

import asyncio
import logging
from typing import Any

logger = logging.getLogger("lyoko.recovery")


class OrphanRecoveryService:
    """Detects and resumes uncompleted incident workflows stored in checkpointer."""

    def __init__(
        self,
        workflow_engine: Any,
        checkpointer: Any = None,
    ) -> None:
        self.workflow_engine = workflow_engine
        self.checkpointer = checkpointer

    async def recover_orphaned_incidents(self) -> list[str]:
        """Scan checkpointer for uncompleted incident threads and resume their execution."""
        if self.checkpointer is None or self.workflow_engine is None:
            logger.debug("No checkpointer or workflow engine configured; skipping orphan recovery.")
            return []

        logger.info("Scanning checkpointer for interrupted incident workflows...")
        recovered_threads: list[str] = []
        seen_threads: set[str] = set()
        candidate_configs: list[dict[str, Any]] = []

        try:
            # Drain the checkpoint iterator into memory first so the underlying cursor
            # and checkpointer lock (e.g. AsyncPostgresSaver.lock) are fully released
            # before querying workflow state, avoiding re-entrant deadlocks.
            async for tuple_item in self.checkpointer.alist(None):
                cfg = tuple_item.config or {}
                t_id = cfg.get("configurable", {}).get("thread_id")
                if not t_id or not isinstance(t_id, str):
                    continue
                if not t_id.startswith("incident-"):
                    continue
                if t_id in seen_threads:
                    continue
                seen_threads.add(t_id)
                candidate_configs.append(cfg)
        except Exception as exc:
            logger.warning("Failed to list checkpoints for orphan recovery: %s", exc)

        for cfg in candidate_configs:
            t_id = cfg.get("configurable", {}).get("thread_id")
            try:
                snapshot = await self.workflow_engine.aget_state(cfg)
                if snapshot and snapshot.next:
                    logger.info(
                        "Found interrupted incident '%s' with pending steps: %s. Resuming...",
                        t_id,
                        snapshot.next,
                    )
                    asyncio.create_task(self._resume_workflow(cfg, t_id))
                    recovered_threads.append(t_id)
            except Exception as exc:
                logger.warning("Error checking state for thread %s: %s", t_id, exc)

        logger.info(
            "Orphan recovery scan finished. Resumed %d incident(s).", len(recovered_threads)
        )
        return recovered_threads

    async def _resume_workflow(self, config: dict[str, Any], thread_id: str) -> None:
        """Asynchronously resume an interrupted incident workflow."""
        try:
            logger.info("Resuming execution for orphaned incident '%s'...", thread_id)
            await self.workflow_engine.ainvoke(None, config=config)
            logger.info("Successfully finished execution for recovered incident '%s'.", thread_id)
        except Exception as exc:
            logger.error(
                "Failed to complete recovered incident '%s': %s",
                thread_id,
                exc,
                exc_info=True,
            )
