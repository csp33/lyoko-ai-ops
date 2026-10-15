"""Unit tests for ChatSessionTracker and session linking in ChatManager."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from lyoko.application.chat.sessions import ChatSessionTracker
from lyoko.domain.interfaces.chat_connector import ChatConnector
from lyoko.domain.models.chat import ApprovalRequest, SentMessage
from lyoko.infrastructure.chat.manager import ChatManager


def _tracker(now: list[datetime], **kwargs) -> ChatSessionTracker:
    return ChatSessionTracker(clock=lambda: now[0], **kwargs)


def _start() -> list[datetime]:
    return [datetime(2026, 10, 2, 20, 0, 0, tzinfo=UTC)]


def test_reuses_session_while_chat_is_active():
    now = _start()
    tracker = _tracker(now, idle_timeout_seconds=900)

    first = tracker.get_session_id("42")
    now[0] += timedelta(minutes=10)
    second = tracker.get_session_id("42")
    now[0] += timedelta(minutes=10)  # idle window is measured from the last activity
    third = tracker.get_session_id("42")

    assert first == second == third
    assert first == "telegram-42-20261002-200000"


def test_rotates_session_after_idle_timeout():
    now = _start()
    tracker = _tracker(now, idle_timeout_seconds=900)

    first = tracker.get_session_id("42")
    now[0] += timedelta(minutes=16)
    second = tracker.get_session_id("42")

    assert first != second
    assert second == "telegram-42-20261002-201600"


def test_zero_idle_timeout_starts_new_session_per_conversation():
    now = _start()
    tracker = _tracker(now, idle_timeout_seconds=0)

    first = tracker.get_session_id("42")
    now[0] += timedelta(seconds=1)
    second = tracker.get_session_id("42")

    assert first != second
    assert second == "telegram-42-20261002-200001"


def test_isolates_chats():
    tracker = ChatSessionTracker()
    assert tracker.get_session_id("1") != tracker.get_session_id("2")


def test_start_new_forces_rotation():
    now = _start()
    tracker = _tracker(now)

    first = tracker.get_session_id("42")
    now[0] += timedelta(seconds=5)
    explicit = tracker.start_new("42")
    now[0] += timedelta(seconds=5)

    assert explicit != first
    assert tracker.get_session_id("42") == explicit


def test_reply_to_linked_message_resumes_linked_session_even_after_idle():
    now = _start()
    tracker = _tracker(now, idle_timeout_seconds=900)
    tracker.link_message("42", 7, "incident-xyz")

    now[0] += timedelta(hours=3)
    assert tracker.get_session_id("42", reply_to_message_id="7") == "incident-xyz"
    # The resumed session becomes the chat's active session.
    now[0] += timedelta(minutes=1)
    assert tracker.get_session_id("42") == "incident-xyz"


def test_reply_to_unlinked_message_falls_back_to_active_session():
    now = _start()
    tracker = _tracker(now)

    active = tracker.get_session_id("42")
    assert tracker.get_session_id("42", reply_to_message_id="999") == active


def test_links_are_scoped_per_chat():
    tracker = ChatSessionTracker()
    tracker.link_message("1", "5", "incident-a")

    assert tracker.get_session_id("2", reply_to_message_id="5") != "incident-a"


def test_linked_messages_are_bounded():
    tracker = ChatSessionTracker(max_linked_messages=2)
    tracker.link_message("1", "1", "s1")
    tracker.link_message("1", "2", "s2")
    tracker.link_message("1", "3", "s3")

    assert tracker.get_session_id("1", reply_to_message_id="1") != "s1"
    assert tracker.get_session_id("1", reply_to_message_id="3") == "s3"


@pytest.mark.asyncio
async def test_chat_manager_links_approval_message_to_incident_session():
    tracker = ChatSessionTracker()
    conn = AsyncMock(spec=ChatConnector)
    conn.send_approval_request.return_value = SentMessage(chat_id="42", message_id="100")
    manager = ChatManager([conn], session_tracker=tracker)

    await manager.broadcast_approval_request(
        ApprovalRequest(incident_id="incident-abc", title="t", details="d", chat_id="42")
    )

    assert tracker.get_session_id("42", reply_to_message_id="100") == "incident-abc"


@pytest.mark.asyncio
async def test_chat_manager_links_report_message_to_given_session():
    tracker = ChatSessionTracker()
    conn = AsyncMock(spec=ChatConnector)
    conn.send_message.return_value = SentMessage(chat_id="42", message_id="200")
    manager = ChatManager([conn], session_tracker=tracker)

    await manager.broadcast_message(chat_id="42", text="report", session_id="incident-abc")

    assert tracker.get_session_id("42", reply_to_message_id="200") == "incident-abc"


@pytest.mark.asyncio
async def test_chat_manager_ignores_connectors_without_message_reference():
    tracker = ChatSessionTracker()
    conn = AsyncMock(spec=ChatConnector)
    conn.send_message.return_value = None
    manager = ChatManager([conn], session_tracker=tracker)

    await manager.broadcast_message(chat_id="42", text="report", session_id="incident-abc")

    assert tracker.get_session_id("42", reply_to_message_id="200") != "incident-abc"
