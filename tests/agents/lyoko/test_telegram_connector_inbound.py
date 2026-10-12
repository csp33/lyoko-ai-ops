import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from lyoko.domain.models.chat import (
    ApprovalAction,
    ApprovalRequest,
    IncomingMessage,
)
from lyoko.infrastructure.chat.streamer import INITIAL_STATUS
from lyoko.infrastructure.chat.telegram import TelegramConnector


def _mock_bot(message_id: int = 555) -> MagicMock:
    """Build a stand-in bot whose placeholder send succeeds and edits are recorded."""
    bot = MagicMock()
    bot.send_message = AsyncMock(return_value=MagicMock(message_id=message_id))
    bot.edit_message_text = AsyncMock()
    bot.delete_message = AsyncMock()
    return bot


def _context(bot: MagicMock) -> MagicMock:
    context = MagicMock()
    context.bot = bot
    return context


@pytest.mark.asyncio
async def test_telegram_connector_incoming_message_handler():
    connector = TelegramConnector(
        bot_token="fake:token", allowed_user_ids={"12345"}, default_chat_id="12345"
    )
    received_msgs: list[IncomingMessage] = []

    async def handler(msg: IncomingMessage) -> str:
        received_msgs.append(msg)
        return "Acknowledged"

    connector.register_message_handler(handler)

    bot = _mock_bot()
    mock_update = MagicMock()
    mock_update.effective_user.id = 12345
    mock_update.effective_user.username = "admin"
    mock_update.effective_user.first_name = "Admin"
    mock_update.effective_chat.id = 12345
    mock_update.message.message_id = 42
    mock_update.message.message_thread_id = None
    mock_update.message.text = "/status"
    mock_update.message.set_reaction = AsyncMock()
    mock_update.message.reply_text = AsyncMock()

    await connector._handle_telegram_message(mock_update, _context(bot))

    assert len(received_msgs) == 1
    assert received_msgs[0].text == "/status"
    assert received_msgs[0].user.user_id == "12345"
    assert received_msgs[0].user.username == "admin"
    mock_update.message.set_reaction.assert_called_once_with(reaction="👀")

    # The progress log is edited in place and finalized into the answer, not a new reply.
    bot.send_message.assert_called_once()
    assert INITIAL_STATUS in bot.send_message.call_args.kwargs["text"]
    assert bot.edit_message_text.call_args.kwargs["text"].endswith("Acknowledged")
    mock_update.message.reply_text.assert_not_called()


@pytest.mark.asyncio
async def test_telegram_connector_reaction_failure_is_logged_and_does_not_block_reply(caplog):
    connector = TelegramConnector(
        bot_token="fake:token", allowed_user_ids={"12345"}, default_chat_id="12345"
    )

    async def handler(msg: IncomingMessage) -> str:
        return "Acknowledged"

    connector.register_message_handler(handler)

    bot = _mock_bot()
    mock_update = MagicMock()
    mock_update.effective_user.id = 12345
    mock_update.effective_user.username = "admin"
    mock_update.effective_user.first_name = "Admin"
    mock_update.effective_chat.id = 12345
    mock_update.message.message_id = 43
    mock_update.message.text = "top 10 clients"
    mock_update.message.set_reaction = AsyncMock(side_effect=RuntimeError("REACTION_INVALID"))
    mock_update.message.reply_text = AsyncMock()

    with caplog.at_level("WARNING", logger="lyoko.chat.telegram"):
        await connector._handle_telegram_message(mock_update, _context(bot))

    assert "Failed to set" in caplog.text
    assert "REACTION_INVALID" in caplog.text
    assert "43" in caplog.text
    assert bot.edit_message_text.call_args.kwargs["text"].endswith("Acknowledged")


@pytest.mark.asyncio
async def test_telegram_connector_message_handler_exception():
    connector = TelegramConnector(
        bot_token="fake:token", allowed_user_ids={"12345"}, default_chat_id="12345"
    )

    async def failing_handler(msg: IncomingMessage) -> str:
        raise ValueError("Handler crash")

    connector.register_message_handler(failing_handler)

    bot = _mock_bot()
    mock_update = MagicMock()
    mock_update.effective_user.id = 12345
    mock_update.effective_user.username = "admin"
    mock_update.effective_user.first_name = "Admin"
    mock_update.effective_chat.id = 12345
    mock_update.message.message_id = 44
    mock_update.message.text = "/crash"
    mock_update.message.reply_text = AsyncMock()

    await connector._handle_telegram_message(mock_update, _context(bot))

    assert "Error processing request" in bot.edit_message_text.call_args.kwargs["text"]


@pytest.mark.asyncio
async def test_telegram_connector_discards_placeholder_without_reply():
    connector = TelegramConnector(
        bot_token="fake:token", allowed_user_ids={"12345"}, default_chat_id="12345"
    )
    received: list[IncomingMessage] = []

    async def handler(msg: IncomingMessage) -> None:
        received.append(msg)

    connector.register_message_handler(handler)

    bot = _mock_bot()
    update = MagicMock()
    update.effective_user.id = 12345
    update.effective_chat.id = 12345
    update.message.message_id = 50
    update.message.text = "why did it crash?"
    update.message.reply_to_message.message_id = 41
    update.message.set_reaction = AsyncMock()
    update.message.reply_text = AsyncMock()

    await connector._handle_telegram_message(update, _context(bot))

    assert received[0].reply_to_message_id == "41"
    bot.delete_message.assert_called_once_with(chat_id="12345", message_id=555)


@pytest.mark.asyncio
async def test_telegram_connector_falls_back_to_reply_text_when_placeholder_fails():
    connector = TelegramConnector(
        bot_token="fake:token", allowed_user_ids={"12345"}, default_chat_id="12345"
    )

    async def handler(msg: IncomingMessage) -> str:
        return "Acknowledged"

    connector.register_message_handler(handler)

    bot = _mock_bot()
    bot.send_message = AsyncMock(side_effect=RuntimeError("placeholder unavailable"))
    update = MagicMock()
    update.effective_user.id = 12345
    update.effective_chat.id = 12345
    update.message.message_id = 60
    update.message.message_thread_id = None
    update.message.text = "hello"
    update.message.set_reaction = AsyncMock()
    update.message.reply_text = AsyncMock()

    await connector._handle_telegram_message(update, _context(bot))

    update.message.reply_text.assert_called_once_with(
        "Acknowledged",
        parse_mode="HTML",
        reply_to_message_id=60,
        message_thread_id=None,
        allow_sending_without_reply=True,
    )


@pytest.mark.asyncio
async def test_telegram_connector_send_returns_message_reference():
    connector = TelegramConnector(bot_token="fake:token", default_chat_id="12345")
    sent = MagicMock()
    sent.message_id = 777
    sent.chat_id = 12345
    mock_app = MagicMock()
    mock_app.bot.send_message = AsyncMock(return_value=sent)
    connector._app = mock_app

    result = await connector.send_message("12345", "hello")
    assert result is not None
    assert (result.chat_id, result.message_id) == ("12345", "777")

    approval = await connector.send_approval_request(
        ApprovalRequest(
            incident_id="inc-1",
            title="t",
            details="d",
            chat_id="12345",
            actions=[ApprovalAction(action_id="approve", label="Approve")],
        )
    )
    assert approval is not None
    assert approval.message_id == "777"


@pytest.mark.asyncio
async def test_telegram_connector_channel_post_redirects_to_discussion_group():
    """Verify replies to channel posts are sent into the linked discussion group comment thread."""
    connector = TelegramConnector(
        bot_token="fake:token",
        allowed_user_ids=set(),
        allowed_chat_ids={"-1001234567890", "-1004400196957"},
        default_chat_id="-1001234567890",
        discussion_group_id="-1004400196957",
    )

    captured: list[IncomingMessage] = []

    async def capture(msg: IncomingMessage) -> str:
        captured.append(msg)
        return "Reply in thread"

    connector.register_message_handler(capture)

    bot = _mock_bot(message_id=999)

    # 1. Simulate the automatic forward of the channel post arriving in the discussion group
    forward_msg = MagicMock()
    forward_msg.message_id = 200
    forward_origin = MagicMock()
    forward_origin.type = "channel"
    forward_origin.chat.id = -1001234567890
    forward_origin.message_id = 50
    forward_msg.forward_origin = forward_origin

    connector._record_channel_forward(forward_msg)

    # 2. Simulate user posting in channel
    channel_update = MagicMock()
    channel_update.message = None
    channel_update.effective_chat.id = -1001234567890
    channel_update.effective_chat.type = "channel"
    channel_update.effective_user = None
    channel_update.effective_message.message_id = 50
    channel_update.effective_message.message_thread_id = None
    channel_update.effective_message.text = "Hello from channel"
    channel_update.effective_message.is_automatic_forward = False
    channel_update.effective_message.set_reaction = AsyncMock()
    channel_update.effective_message.reply_text = AsyncMock()

    await connector._handle_telegram_message(channel_update, _context(bot))

    # The handler receives the resolved target so approvals and progress thread correctly.
    assert captured[0].chat_id == "-1004400196957"
    assert captured[0].message_thread_id == "200"

    # The placeholder goes to the discussion group, not the channel.
    channel_update.effective_message.reply_text.assert_not_called()
    bot.send_message.assert_called_once()
    call_kwargs = bot.send_message.call_args.kwargs
    assert call_kwargs["chat_id"] == "-1004400196957"
    assert call_kwargs["reply_to_message_id"] == 200
    assert call_kwargs["message_thread_id"] == 200

    # The placeholder is finalized with the answer appended below the checklist.
    assert bot.edit_message_text.call_args.kwargs["text"].endswith("Reply in thread")


@pytest.mark.asyncio
async def test_channel_post_waits_for_late_discussion_forward_to_thread_reply():
    """The post and its discussion forward race; a late forward must still thread the reply."""
    connector = TelegramConnector(
        bot_token="fake:token",
        allowed_user_ids=set(),
        allowed_chat_ids={"-1001234567890", "-1004400196957"},
        default_chat_id="-1001234567890",
        discussion_group_id="-1004400196957",
    )
    connector.discussion_mapping_interval = 0.01
    connector.discussion_mapping_timeout = 1.0

    async def handler(msg: IncomingMessage) -> str:
        return "Reply in thread"

    connector.register_message_handler(handler)

    bot = _mock_bot(message_id=999)

    forward_msg = MagicMock()
    forward_msg.message_id = 200
    forward_origin = MagicMock()
    forward_origin.type = "channel"
    forward_origin.chat.id = -1001234567890
    forward_origin.message_id = 50
    forward_msg.forward_origin = forward_origin

    # The forward lands after the channel post, exactly like the real Telegram race.
    asyncio.get_event_loop().call_later(0.03, connector._record_channel_forward, forward_msg)

    channel_update = MagicMock()
    channel_update.message = None
    channel_update.effective_chat.id = -1001234567890
    channel_update.effective_chat.type = "channel"
    channel_update.effective_user = None
    channel_update.effective_message.message_id = 50
    channel_update.effective_message.message_thread_id = None
    channel_update.effective_message.text = "Hello from channel"
    channel_update.effective_message.is_automatic_forward = False
    channel_update.effective_message.set_reaction = AsyncMock()
    channel_update.effective_message.reply_text = AsyncMock()

    await connector._handle_telegram_message(channel_update, _context(bot))

    call_kwargs = bot.send_message.call_args.kwargs
    assert call_kwargs["chat_id"] == "-1004400196957"
    assert call_kwargs["reply_to_message_id"] == 200
    assert call_kwargs["message_thread_id"] == 200


@pytest.mark.asyncio
async def test_channel_post_proceeds_after_mapping_timeout_without_forward():
    """A channel post with no discussion forward must still be answered, not wait forever."""
    connector = TelegramConnector(
        bot_token="fake:token",
        allowed_user_ids=set(),
        allowed_chat_ids={"-1001234567890", "-1004400196957"},
        default_chat_id="-1001234567890",
        discussion_group_id="-1004400196957",
    )
    connector.discussion_mapping_interval = 0.01
    connector.discussion_mapping_timeout = 0.05

    async def handler(msg: IncomingMessage) -> str:
        return "Reply in thread"

    connector.register_message_handler(handler)

    bot = _mock_bot(message_id=999)

    channel_update = MagicMock()
    channel_update.message = None
    channel_update.effective_chat.id = -1001234567890
    channel_update.effective_chat.type = "channel"
    channel_update.effective_user = None
    channel_update.effective_message.message_id = 50
    channel_update.effective_message.message_thread_id = None
    channel_update.effective_message.text = "Hello from channel"
    channel_update.effective_message.is_automatic_forward = False
    channel_update.effective_message.set_reaction = AsyncMock()
    channel_update.effective_message.reply_text = AsyncMock()

    await connector._handle_telegram_message(channel_update, _context(bot))

    call_kwargs = bot.send_message.call_args.kwargs
    assert call_kwargs["chat_id"] == "-1004400196957"
    assert call_kwargs["reply_to_message_id"] == 50


@pytest.mark.asyncio
async def test_telegram_connector_stops_immediately_when_stop_button_clicked():
    """When stop is triggered during message handling, execution task is cancelled and stopped notice finalized."""
    connector = TelegramConnector(
        bot_token="fake:token", allowed_user_ids={"12345"}, default_chat_id="12345"
    )

    task_started = asyncio.Event()

    async def slow_handler(msg: IncomingMessage, on_status=None) -> str:
        task_started.set()
        await asyncio.sleep(60)
        return "Should not arrive"

    connector.register_message_handler(slow_handler)

    bot = _mock_bot(message_id=555)
    update = MagicMock()
    update.effective_user.id = 12345
    update.effective_user.username = "admin"
    update.effective_user.first_name = "Admin"
    update.effective_chat.id = 12345
    update.effective_chat.type = "private"
    update.message.message_id = 42
    update.message.message_thread_id = None
    update.message.text = "Hello"
    update.message.set_reaction = AsyncMock()

    msg_task = asyncio.create_task(connector._handle_telegram_message(update, _context(bot)))
    await task_started.wait()

    assert len(connector._active_streamers) == 1
    streamer = list(connector._active_streamers.values())[0]
    streamer.stop()

    await msg_task

    texts = [call.kwargs["text"] for call in bot.edit_message_text.call_args_list]
    assert any("Generation stopped" in t for t in texts)
