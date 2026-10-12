"""Unit tests for PostgreSQL Advisory Lock distributed leader election."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from lyoko.infrastructure.db.lock import PostgresAdvisoryLock


@pytest.mark.asyncio
async def test_postgres_advisory_lock_standalone_without_pool():
    """When pool is None, lock behaves as no-op and acquires immediately."""
    lock = PostgresAdvisoryLock(pool=None)
    assert not lock.is_locked

    acquired = await lock.acquire()
    assert acquired is True
    assert lock.is_locked is True

    await lock.release()
    assert lock.is_locked is False


@pytest.mark.asyncio
async def test_postgres_advisory_lock_acquire_and_release_success():
    """Lock acquires via pg_try_advisory_lock and releases via pg_advisory_unlock."""
    mock_cursor = AsyncMock()
    mock_cursor.fetchone.return_value = (True,)

    mock_conn = MagicMock()
    mock_conn.closed = False
    mock_conn.cursor.return_value.__aenter__.return_value = mock_cursor

    mock_pool = AsyncMock()
    mock_pool.getconn.return_value = mock_conn

    lock = PostgresAdvisoryLock(pool=mock_pool, lock_id=12345)
    acquired = await lock.acquire()

    assert acquired is True
    assert lock.is_locked is True
    mock_cursor.execute.assert_called_with("SELECT pg_try_advisory_lock(%s)", (12345,))

    await lock.release()
    assert lock.is_locked is False
    mock_cursor.execute.assert_called_with("SELECT pg_advisory_unlock(%s)", (12345,))
    mock_pool.putconn.assert_called_with(mock_conn)


@pytest.mark.asyncio
async def test_postgres_advisory_lock_timeout_when_contended():
    """Lock times out when another session holds the lock."""
    mock_cursor = AsyncMock()
    mock_cursor.fetchone.return_value = (False,)  # Lock not granted

    mock_conn = MagicMock()
    mock_conn.closed = False
    mock_conn.cursor.return_value.__aenter__.return_value = mock_cursor

    mock_pool = AsyncMock()
    mock_pool.getconn.return_value = mock_conn

    lock = PostgresAdvisoryLock(pool=mock_pool, lock_id=99999, retry_interval=0.05)
    acquired = await lock.acquire(timeout=0.15)

    assert acquired is False
    assert lock.is_locked is False
    assert mock_pool.putconn.call_count >= 1


@pytest.mark.asyncio
async def test_postgres_advisory_lock_retry_and_eventual_acquisition():
    """Lock retries and succeeds when lock becomes available on next tick."""
    mock_cursor = AsyncMock()
    # First call False, second call True
    mock_cursor.fetchone.side_effect = [(False,), (True,)]

    mock_conn = MagicMock()
    mock_conn.closed = False
    mock_conn.cursor.return_value.__aenter__.return_value = mock_cursor

    mock_pool = AsyncMock()
    mock_pool.getconn.return_value = mock_conn

    lock = PostgresAdvisoryLock(pool=mock_pool, lock_id=88888, retry_interval=0.02)
    acquired = await lock.acquire(timeout=0.2)

    assert acquired is True
    assert lock.is_locked is True
    assert mock_cursor.execute.call_count == 2
