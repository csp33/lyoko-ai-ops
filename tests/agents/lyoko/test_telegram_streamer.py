import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from lyoko.infrastructure.chat.streamer import TelegramStreamingReply
from telegram.error import RetryAfter


def _fake_bot(message_id: int = 555) -> MagicMock:
    bot = MagicMock()
    sent = MagicMock(message_id=message_id)
    bot.send_message = AsyncMock(return_value=sent)
    bot.edit_message_text = AsyncMock()
    bot.delete_message = AsyncMock()
    return bot


@pytest.mark.asyncio
async def test_start_sends_threaded_checklist_with_stop_button():
    """Verify the placeholder is sent threaded to the incoming message with a stop button."""
    bot = _fake_bot()
    streamer = TelegramStreamingReply(
        bot=bot, chat_id="12345", reply_to_message_id="42", message_thread_id="7"
    )

    await streamer.start()

    bot.send_message.assert_called_once()
    kwargs = bot.send_message.call_args.kwargs
    assert kwargs["chat_id"] == "12345"
    assert kwargs["reply_to_message_id"] == 42
    assert kwargs["message_thread_id"] == 7
    assert kwargs["reply_markup"] is not None
    assert kwargs["parse_mode"] == "HTML"
    assert "Progress" in kwargs["text"]
    assert "· 0s" in kwargs["text"]
    assert streamer.message_id == 555
    await streamer._cancel_status()


@pytest.mark.asyncio
async def test_set_status_appends_checklist_steps():
    """Verify each factual status appends a step: completed steps check, active step timer."""
    bot = _fake_bot()
    streamer = TelegramStreamingReply(bot=bot, chat_id="12345", progress_edit_gap_seconds=0)
    await streamer.start()
    bot.edit_message_text.reset_mock()

    await streamer.set_status("🧩 Consulting the kubernetes specialist")
    await streamer.set_status("🛰️ Calling <code>kubectl_get</code>")

    last_kwargs = bot.edit_message_text.call_args.kwargs
    last = last_kwargs["text"]
    assert "🧩 Consulting the kubernetes specialist" in last
    assert last.count("✔") == 2
    assert last.count("⏳") == 1
    assert "🛰️ Calling <code>kubectl_get</code>" in last
    assert last_kwargs["parse_mode"] == "HTML"
    assert last_kwargs["reply_markup"] is not None
    button = last_kwargs["reply_markup"].inline_keyboard[0][0]
    assert button.text == "⏹ Stop"
    assert button.callback_data == f"stop:{streamer.draft_id}"


@pytest.mark.asyncio
async def test_set_status_ignores_duplicate_and_post_stop():
    """Verify duplicate statuses and statuses after stop are ignored."""
    bot = _fake_bot()
    streamer = TelegramStreamingReply(bot=bot, chat_id="12345")
    await streamer.start()

    await streamer.set_status("🧩 Consulting the kubernetes specialist")
    bot.edit_message_text.reset_mock()
    await streamer.set_status("🧩 Consulting the kubernetes specialist")
    bot.edit_message_text.assert_not_called()

    streamer.stop()
    await streamer.set_status("🛰️ Calling <code>x</code>")
    bot.edit_message_text.assert_not_called()


@pytest.mark.asyncio
async def test_finalize_shows_done_then_replaces_with_answer():
    """Verify finalize swaps the checklist for Done, then replaces it with the answer."""
    bot = _fake_bot()
    streamer = TelegramStreamingReply(bot=bot, chat_id="12345", finalize_delay_seconds=0)
    await streamer.start()
    await streamer.set_status("🛰️ Calling <code>kubectl_get</code>")
    bot.edit_message_text.reset_mock()

    ok = await streamer.finalize("<b>Ack</b>")

    assert ok is True
    assert bot.edit_message_text.call_count == 2
    done_kwargs = bot.edit_message_text.call_args_list[0].kwargs
    assert "✅" in done_kwargs["text"] and "Done" in done_kwargs["text"]
    assert "kubectl_get" in done_kwargs["text"]
    assert done_kwargs["reply_markup"] is None
    answer_kwargs = bot.edit_message_text.call_args_list[1].kwargs
    assert answer_kwargs["text"] == "<b>Ack</b>"
    assert answer_kwargs["parse_mode"] == "HTML"


@pytest.mark.asyncio
async def test_finalize_waits_before_replacing_with_answer():
    """Verify the Done marker stays visible for the configured delay before the swap."""
    bot = _fake_bot()
    streamer = TelegramStreamingReply(bot=bot, chat_id="12345", finalize_delay_seconds=0.05)
    await streamer.start()
    bot.edit_message_text.reset_mock()

    await asyncio.sleep(0)
    finalize_task = asyncio.create_task(streamer.finalize("<b>Ack</b>"))
    await asyncio.sleep(0.01)

    # Only the Done marker has been written so far.
    assert bot.edit_message_text.call_count == 1
    assert "Done" in bot.edit_message_text.call_args.kwargs["text"]

    await finalize_task
    assert bot.edit_message_text.call_args.kwargs["text"] == "<b>Ack</b>"


@pytest.mark.asyncio
async def test_finalize_drops_checklist_when_answer_exceeds_limit():
    """Verify an answer that would overflow the limit is sent alone, without the checklist."""
    from lyoko.infrastructure.chat.streamer import MAX_MESSAGE_LENGTH

    bot = _fake_bot()
    streamer = TelegramStreamingReply(bot=bot, chat_id="12345", finalize_delay_seconds=0)
    await streamer.start()
    bot.edit_message_text.reset_mock()

    await streamer.finalize("A" * MAX_MESSAGE_LENGTH)

    text = bot.edit_message_text.call_args.kwargs["text"]
    assert "Progress" not in text
    assert text.startswith("A")


@pytest.mark.asyncio
async def test_finalize_falls_back_to_plain_on_html_error():
    """Verify a rejected HTML markup is retried as plain text."""

    def _edit_raises_on_html(*args, **kwargs):  # noqa: ANN002, ANN003
        if kwargs.get("parse_mode"):
            raise RuntimeError("bad html")
        return None

    bot = _fake_bot()
    bot.edit_message_text = AsyncMock(side_effect=_edit_raises_on_html)
    streamer = TelegramStreamingReply(bot=bot, chat_id="12345", finalize_delay_seconds=0)
    await streamer.start()

    ok = await streamer.finalize("<b>x</b>")

    assert ok is True
    assert "parse_mode" not in bot.edit_message_text.call_args.kwargs
    assert bot.edit_message_text.call_args.kwargs["text"] == "x"


@pytest.mark.asyncio
async def test_finalize_waits_out_flood_control_and_delivers_the_answer(monkeypatch):
    """A throttled final edit must be retried after the flood wait, not dropped."""
    bot = _fake_bot()
    answer_state = {"attempts": 0}

    async def edit(**kwargs):
        if kwargs.get("text") == "<b>Ack</b>":
            answer_state["attempts"] += 1
            if answer_state["attempts"] == 1:
                raise RetryAfter(7)
        return None

    bot.edit_message_text = AsyncMock(side_effect=edit)
    slept: list[float] = []

    async def fake_sleep(seconds):  # noqa: ANN001
        slept.append(seconds)

    monkeypatch.setattr("lyoko.infrastructure.chat.streamer.asyncio.sleep", fake_sleep)

    streamer = TelegramStreamingReply(bot=bot, chat_id="12345", finalize_delay_seconds=0)
    await streamer.start()

    assert await streamer.finalize("<b>Ack</b>") is True
    assert answer_state["attempts"] == 2
    assert slept and slept[0] >= 7


@pytest.mark.asyncio
async def test_start_failure_disables_streaming():
    """Verify a failed placeholder disables streaming so the caller falls back to a reply."""
    bot = _fake_bot()
    bot.send_message = AsyncMock(side_effect=RuntimeError("boom"))
    streamer = TelegramStreamingReply(bot=bot, chat_id="12345")

    await streamer.start()

    assert streamer.message_id is None
    assert streamer._disabled is True
    await streamer.set_status("x")
    bot.edit_message_text.assert_not_called()
    assert await streamer.finalize("y") is False


@pytest.mark.asyncio
async def test_stop_freezes_checklist_with_note():
    """Verify stop() freezes the checklist and appends a stopped note."""
    bot = _fake_bot()
    streamer = TelegramStreamingReply(bot=bot, chat_id="12345")
    await streamer.start()
    await streamer.set_status("🛰️ Calling <code>kubectl_get</code>")
    streamer.stop()

    assert streamer.is_stopped() is True

    bot.edit_message_text.reset_mock()
    await streamer.finalize_stopped()
    text = bot.edit_message_text.call_args.kwargs["text"]
    assert "Generation stopped" in text
    assert "🛰️ Calling <code>kubectl_get</code>" in text


@pytest.mark.asyncio
async def test_status_loop_refreshes_active_step_timer():
    """Verify the background loop keeps the active step's elapsed timer live."""
    bot = _fake_bot()
    streamer = TelegramStreamingReply(
        bot=bot,
        chat_id="12345",
        status_interval_seconds=0.01,
        finalize_delay_seconds=0,
        progress_edit_gap_seconds=0,
    )
    await streamer.start()
    bot.edit_message_text.reset_mock()

    await asyncio.sleep(0.05)

    assert bot.edit_message_text.call_count >= 1
    call_kwargs = bot.edit_message_text.call_args.kwargs
    assert "Progress" in call_kwargs["text"]
    assert call_kwargs["reply_markup"] is not None
    button = call_kwargs["reply_markup"].inline_keyboard[0][0]
    assert button.text == "⏹ Stop"
    assert button.callback_data == f"stop:{streamer.draft_id}"
    await streamer.finalize("<b>done</b>")


@pytest.mark.asyncio
async def test_progress_timer_lives_in_the_header_not_on_the_active_step():
    """A slow tool must not look stuck: elapsed time belongs to the header, not the step."""
    bot = _fake_bot()
    streamer = TelegramStreamingReply(
        bot=bot, chat_id="12345", status_interval_seconds=0.01, finalize_delay_seconds=0
    )
    await streamer.start()
    await streamer.set_status("🛰️ Calling <code>ha_get_logs</code>")
    await asyncio.sleep(0.05)

    text = bot.edit_message_text.call_args.kwargs["text"]
    assert "<b>🧠 Progress</b> ·" in text
    active_line = next(line for line in text.splitlines() if line.startswith("⏳"))
    assert "·" not in active_line
    await streamer.finalize("<b>done</b>")


@pytest.mark.asyncio
async def test_discard_deletes_placeholder():
    """Verify discard removes the placeholder when no answer replaces it."""
    bot = _fake_bot()
    streamer = TelegramStreamingReply(bot=bot, chat_id="12345")
    await streamer.start()

    await streamer.discard()

    bot.delete_message.assert_called_once_with(chat_id="12345", message_id=555)


@pytest.mark.asyncio
async def test_stop_cancels_bound_execution_task():
    """Verify stop() immediately cancels any bound in-flight execution task."""
    bot = _fake_bot()
    streamer = TelegramStreamingReply(bot=bot, chat_id="12345")
    await streamer.start()

    async def _long_running():
        await asyncio.sleep(60)

    task = asyncio.create_task(_long_running())
    streamer.bind_task(task)

    assert not task.done()
    streamer.stop()

    assert streamer.is_stopped()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert task.cancelled()
