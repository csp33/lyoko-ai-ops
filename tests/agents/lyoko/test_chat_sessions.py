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


def test_threads_have_isolated_sessions_in_same_chat():
    now = _start()
    tracker = _tracker(now, idle_timeout_seconds=900)

    topic_a_first = tracker.get_session_id("42", thread_id="101")
    topic_b_first = tracker.get_session_id("42", thread_id="202")
    main_chat = tracker.get_session_id("42")

    # All three are isolated from each other
    assert topic_a_first != topic_b_first
    assert topic_a_first != main_chat
    assert topic_b_first != main_chat
    assert topic_a_first.startswith("telegram-42-101-")
    assert topic_b_first.startswith("telegram-42-202-")

    # Subsequent messages in topic A stay in topic A's session
    now[0] += timedelta(minutes=5)
    topic_a_second = tracker.get_session_id("42", thread_id="101")
    assert topic_a_second == topic_a_first

    # Subsequent messages in topic B stay in topic B's session
    topic_b_second = tracker.get_session_id("42", thread_id="202")
    assert topic_b_second == topic_b_first


def test_thread_initiated_by_linked_message_resumes_linked_session():
    now = _start()
    tracker = _tracker(now, idle_timeout_seconds=900)
    tracker.link_message("42", "555", "incident-gpu-oom")

    # When a discussion thread is created whose thread_id matches the linked post
    session = tracker.get_session_id("42", thread_id="555")
    assert session == "incident-gpu-oom"

    # Follow-up in that thread continues the incident session
    now[0] += timedelta(minutes=10)
    assert tracker.get_session_id("42", thread_id="555") == "incident-gpu-oom"


def test_start_new_is_scoped_to_thread():
    now = _start()
    tracker = _tracker(now, idle_timeout_seconds=900)

    orig_a = tracker.get_session_id("42", thread_id="101")
    orig_b = tracker.get_session_id("42", thread_id="202")

    # Reset only topic A after some time
    now[0] += timedelta(seconds=5)
    new_a = tracker.start_new("42", thread_id="101")
    assert new_a != orig_a
    assert tracker.get_session_id("42", thread_id="101") == new_a

    # Topic B is untouched
    assert tracker.get_session_id("42", thread_id="202") == orig_b


def test_message_linking_enables_resuming_session_by_reply():
    now = _start()
    tracker = _tracker(now, idle_timeout_seconds=900)

    # First turn in chat
    sess = tracker.get_session_id("42")
    user_msg_id = "1001"
    bot_msg_id = "1002"
    tracker.link_message("42", user_msg_id, sess)
    tracker.link_message("42", bot_msg_id, sess)

    # Simulate hours passing, session idle timeout exceeded
    now[0] += timedelta(hours=5)

    # Replying to bot's response resumes the exact same session
    assert tracker.get_session_id("42", reply_to_message_id=bot_msg_id) == sess

    # Replying to user's original message also resumes it
    assert tracker.get_session_id("42", reply_to_message_id=user_msg_id) == sess
