"""PostgreSQL Advisory Lock implementation for distributed leader election."""

import asyncio
import contextlib
import logging
from typing import Any

from lyoko.domain.interfaces.lock import DistributedLock
from psycopg_pool import AsyncConnectionPool

logger = logging.getLogger("lyoko.db.lock")

# Deterministic 64-bit integer ID for Telegram polling leader election
DEFAULT_TELEGRAM_LOCK_ID: int = 42_108_001


class PostgresAdvisoryLock(DistributedLock):
    """Distributed lock adapter using PostgreSQL session-level advisory locks."""

    def __init__(
        self,
        pool: AsyncConnectionPool | None = None,
        lock_id: int = DEFAULT_TELEGRAM_LOCK_ID,
        retry_interval: float = 1.0,
    ) -> None:
        self.pool = pool
        self.lock_id = lock_id
        self.retry_interval = retry_interval
        self._conn: Any = None
        self._is_locked: bool = False
        self._stop_event = asyncio.Event()

    @property
    def is_locked(self) -> bool:
        """Check whether the current instance holds the advisory lock."""
        return self._is_locked

    async def acquire(self, timeout: float | None = None) -> bool:
        """Attempt to acquire the advisory lock, retrying until timeout or stopped."""
        if self.pool is None:
            logger.debug(
                "No PostgreSQL pool configured; acquiring lock immediately in standalone mode."
            )
            self._is_locked = True
            return True

        loop = asyncio.get_running_loop()
        start_time = loop.time()
        self._stop_event.clear()

        while not self._stop_event.is_set():
            if timeout is not None:
                elapsed = loop.time() - start_time
                if elapsed >= timeout:
                    logger.debug("Timed out waiting for PostgreSQL advisory lock %s", self.lock_id)
                    return False

            conn = None
            try:
                conn = await self.pool.getconn()
                async with conn.cursor() as cur:
                    await cur.execute("SELECT pg_try_advisory_lock(%s)", (self.lock_id,))
                    row = await cur.fetchone()
                    acquired = bool(row[0]) if row else False

                if acquired:
                    self._conn = conn
                    self._is_locked = True
                    logger.info("Successfully acquired PostgreSQL advisory lock %s", self.lock_id)
                    return True

                await self.pool.putconn(conn)
            except Exception as exc:
                logger.warning("Error checking PostgreSQL advisory lock %s: %s", self.lock_id, exc)
                if conn is not None:
                    with contextlib.suppress(Exception):
                        await self.pool.putconn(conn)

            # Calculate sleep duration respecting remaining timeout if set
            sleep_duration = self.retry_interval
            if timeout is not None:
                remaining = timeout - (loop.time() - start_time)
                if remaining <= 0:
                    return False
                sleep_duration = min(sleep_duration, remaining)

            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._stop_event.wait(), timeout=sleep_duration)

        return False

    async def release(self) -> None:
        """Release the PostgreSQL advisory lock and return connection to pool."""
        self._stop_event.set()
        if self._conn is not None:
            try:
                if not getattr(self._conn, "closed", False):
                    async with self._conn.cursor() as cur:
                        await cur.execute("SELECT pg_advisory_unlock(%s)", (self.lock_id,))
                    logger.info("Released PostgreSQL advisory lock %s", self.lock_id)
            except Exception as exc:
                logger.warning("Error releasing PostgreSQL advisory lock %s: %s", self.lock_id, exc)
            finally:
                if self.pool:
                    with contextlib.suppress(Exception):
                        await self.pool.putconn(self._conn)
                self._conn = None
        self._is_locked = False
