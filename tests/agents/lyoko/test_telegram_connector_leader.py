"""Unit tests for TelegramConnector distributed leader election and polling handover."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from lyoko.domain.interfaces.lock import DistributedLock
from lyoko.infrastructure.chat.telegram import TelegramConnector


@pytest.mark.asyncio
async def test_telegram_connector_starts_polling_immediately_without_lock():
    """Without a lock, updater.start_polling() is invoked synchronously during start()."""
    connector = TelegramConnector(bot_token="test:token", lock=None)

    mock_app = MagicMock()
    mock_app.initialize = AsyncMock()
    mock_app.start = AsyncMock()
    mock_app.updater = MagicMock()
    mock_app.updater.start_polling = AsyncMock()

    with patch("lyoko.infrastructure.chat.telegram.Application.builder") as mock_builder:
        mock_builder.return_value.token.return_value.concurrent_updates.return_value.build.return_value = mock_app
        await connector.start()

    mock_app.updater.start_polling.assert_called_once()
    assert connector._leader_polling_task is None


@pytest.mark.asyncio
async def test_telegram_connector_waits_for_leader_lock_before_polling():
    """With a lock, polling only begins once the distributed lock is acquired."""
    mock_lock = MagicMock(spec=DistributedLock)
    # Simulate waiting briefly before acquiring lock
    lock_acquired_event = asyncio.Event()

    async def mock_acquire(timeout=None):
        await lock_acquired_event.wait()
        return True

    mock_lock.acquire = mock_acquire
    mock_lock.is_locked = False

    connector = TelegramConnector(bot_token="test:token", lock=mock_lock)

    mock_app = MagicMock()
    mock_app.initialize = AsyncMock()
    mock_app.start = AsyncMock()
    mock_app.stop = AsyncMock()
    mock_app.shutdown = AsyncMock()
    mock_app.updater = MagicMock()
    mock_app.updater.running = False
    mock_app.updater.start_polling = AsyncMock()
    mock_app.updater.stop = AsyncMock()

    with patch("lyoko.infrastructure.chat.telegram.Application.builder") as mock_builder:
        mock_builder.return_value.token.return_value.concurrent_updates.return_value.build.return_value = mock_app
        await connector.start()

    # Immediately after start, polling has NOT started yet
    mock_app.updater.start_polling.assert_not_called()
    assert connector._leader_polling_task is not None

    # Now unlock / grant leadership
    lock_acquired_event.set()
    # Give the task loop a cycle to run
    await asyncio.sleep(0.01)

    mock_app.updater.start_polling.assert_called_once()

    # Clean up
    await connector.stop()


@pytest.mark.asyncio
async def test_telegram_connector_stop_cancels_leader_task_and_releases_lock():
    """Stopping the connector cancels background polling tasks and releases acquired locks."""
    mock_lock = MagicMock(spec=DistributedLock)
    mock_lock.is_locked = True
    mock_lock.release = AsyncMock()

    connector = TelegramConnector(bot_token="test:token", lock=mock_lock)

    # Fake in-flight polling task
    async def infinite_task():
        try:
            await asyncio.sleep(100)
        except asyncio.CancelledError:
            raise

    task = asyncio.create_task(infinite_task())
    connector._leader_polling_task = task

    mock_app = MagicMock()
    mock_app.updater = MagicMock()
    mock_app.updater.running = True
    mock_app.updater.stop = AsyncMock()
    mock_app.stop = AsyncMock()
    mock_app.shutdown = AsyncMock()
    connector._app = mock_app

    await connector.stop()

    assert task.cancelled() or task.done()
    assert connector._leader_polling_task is None
    mock_app.updater.stop.assert_called_once()
    mock_app.stop.assert_called_once()
    mock_app.shutdown.assert_called_once()
    mock_lock.release.assert_called_once()
