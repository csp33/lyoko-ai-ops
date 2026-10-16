from unittest.mock import AsyncMock, MagicMock

import pytest
from lyoko.domain.models.chat import (
    ApprovalResponse,
)
from lyoko.infrastructure.chat.telegram import TelegramConnector


@pytest.mark.asyncio
async def test_telegram_connector_callback_query_approval():
    connector = TelegramConnector(
        bot_token="fake:token", allowed_user_ids={"12345"}, default_chat_id="12345"
    )
    received_approvals: list[ApprovalResponse] = []

    async def approval_handler(resp: ApprovalResponse) -> None:
        received_approvals.append(resp)

    connector.register_approval_handler(approval_handler)

    mock_update = MagicMock()
    mock_update.effective_user.id = 12345
    mock_query = MagicMock()
    mock_query.data = "approve:inc-abc"
    mock_query.message.text = "Approval prompt"
    mock_query.answer = AsyncMock()
    mock_query.edit_message_text = AsyncMock()
    mock_update.callback_query = mock_query

    await connector._handle_callback_query(mock_update, MagicMock())

    mock_query.answer.assert_called_once()
    assert len(received_approvals) == 1
    assert received_approvals[0].incident_id == "inc-abc"
    assert received_approvals[0].approved is True
    assert received_approvals[0].user_id == "12345"
    assert received_approvals[0].action_id == "approve"
    mock_query.edit_message_text.assert_called_once()
    assert "Approved" in mock_query.edit_message_text.call_args[0][0]


@pytest.mark.asyncio
async def test_callback_preserves_approval_markup():
    """Telegram returns an answered message's text unformatted, so the edit must reuse the sent markup."""
    connector = TelegramConnector(
        bot_token="fake:token", allowed_user_ids={"12345"}, default_chat_id="12345"
    )
    connector._approval_message_html[("12345", 42)] = (
        "🚨 <b>[APPROVAL REQUIRED]</b>\n\n<b>Action:</b> Apply changes to Application 'arr-stack'.",
        "HTML",
    )

    mock_update = MagicMock()
    mock_update.effective_user.id = 12345
    mock_update.effective_user.first_name = "Admin"
    mock_update.effective_chat.id = 12345
    mock_query = MagicMock()
    mock_query.data = "approve:inc-1"
    mock_query.message.message_id = 42
    mock_query.message.text = "🚨 [APPROVAL REQUIRED]\n\nAction: Apply changes."
    mock_query.answer = AsyncMock()
    mock_query.edit_message_text = AsyncMock()
    mock_update.callback_query = mock_query

    await connector._handle_callback_query(mock_update, MagicMock())

    edited = mock_query.edit_message_text.call_args[0][0]
    assert "<b>[APPROVAL REQUIRED]</b>" in edited
    assert "<b>Action:</b>" in edited
    assert mock_query.edit_message_text.call_args[1]["parse_mode"] == "HTML"
    assert ("12345", 42) not in connector._approval_message_html


@pytest.mark.asyncio
async def test_telegram_connector_callback_query_rejection():
    connector = TelegramConnector(
        bot_token="fake:token", allowed_user_ids={"12345"}, default_chat_id="12345"
    )
    received_approvals: list[ApprovalResponse] = []

    async def approval_handler(resp: ApprovalResponse) -> None:
        received_approvals.append(resp)

    connector.register_approval_handler(approval_handler)

    mock_update = MagicMock()
    mock_update.effective_user.id = 12345
    mock_query = MagicMock()
    mock_query.data = "reject:inc-abc"
    mock_query.message.text = "Approval prompt"
    mock_query.answer = AsyncMock()
    mock_query.edit_message_text = AsyncMock()
    mock_update.callback_query = mock_query

    await connector._handle_callback_query(mock_update, MagicMock())

    assert "Rejected" in mock_query.edit_message_text.call_args[0][0]


@pytest.mark.asyncio
async def test_telegram_connector_callback_query_force():
    connector = TelegramConnector(
        bot_token="fake:token", allowed_user_ids={"12345"}, default_chat_id="12345"
    )
    received_approvals: list[ApprovalResponse] = []

    async def approval_handler(resp: ApprovalResponse) -> None:
        received_approvals.append(resp)

    connector.register_approval_handler(approval_handler)

    mock_update = MagicMock()
    mock_update.effective_user.id = 12345
    mock_update.effective_user.first_name = "Admin"
    mock_query = MagicMock()
    mock_query.data = "force:inc-abc"
    mock_query.message.text = "Alert suppressed in cooldown"
    mock_query.answer = AsyncMock()
    mock_query.edit_message_text = AsyncMock()
    mock_update.callback_query = mock_query

    await connector._handle_callback_query(mock_update, MagicMock())

    mock_query.answer.assert_called_once()
    assert len(received_approvals) == 1
    assert received_approvals[0].incident_id == "inc-abc"
    assert received_approvals[0].approved is True
    assert received_approvals[0].action_id == "force"
    mock_query.edit_message_text.assert_called_once()
    assert "Investigation forced" in mock_query.edit_message_text.call_args[0][0]


@pytest.mark.asyncio
async def test_telegram_connector_callback_query_unauthorized():
    connector = TelegramConnector(
        bot_token="fake:token", allowed_user_ids={"12345"}, default_chat_id="12345"
    )
    mock_handler = AsyncMock()
    connector.register_approval_handler(mock_handler)

    mock_update = MagicMock()
    mock_update.effective_user.id = 99999
    mock_query = MagicMock()
    mock_query.data = "approve:inc-abc"
    mock_query.answer = AsyncMock()
    mock_update.callback_query = mock_query

    await connector._handle_callback_query(mock_update, MagicMock())

    mock_query.answer.assert_called_once_with("⛔ Unauthorized", show_alert=True)
    mock_handler.assert_not_called()


@pytest.mark.asyncio
async def test_telegram_connector_feedback_callback_button():
    """Verify clicking feedback/teach button sets redirection mode."""
    connector = TelegramConnector(
        bot_token="fake:token", allowed_user_ids={"12345"}, default_chat_id="12345"
    )
    mock_approval_handler = AsyncMock()
    connector.register_approval_handler(mock_approval_handler)

    mock_update = MagicMock()
    mock_update.effective_user.id = 12345
    mock_update.effective_user.first_name = "Admin"
    mock_update.effective_chat.id = 12345
    mock_update.callback_query.data = "feedback:incident-influxdb-0"
    mock_update.callback_query.answer = AsyncMock()
    mock_update.callback_query.edit_message_text = AsyncMock()
    mock_update.callback_query.message.text = "Remediation approval required"

    await connector._handle_callback_query(mock_update, MagicMock())

    assert mock_approval_handler.called
    resp: ApprovalResponse = mock_approval_handler.call_args[0][0]
    assert resp.incident_id == "incident-influxdb-0"
    assert resp.approved is False
    assert resp.action_id == "feedback"
    assert "redirection" in resp.reason.lower()
    assert mock_update.callback_query.edit_message_text.called
    edited_text = mock_update.callback_query.edit_message_text.call_args[0][0]
    assert "Teaching mode activated" in edited_text


@pytest.mark.asyncio
async def test_telegram_connector_stop_callback():
    """Verify clicking Stop aborts the in-flight streaming reply and clears the button."""
    connector = TelegramConnector(
        bot_token="fake:token", allowed_user_ids={"12345"}, default_chat_id="12345"
    )
    streamer = MagicMock()
    connector._active_streamers[("12345", 555)] = streamer

    mock_update = MagicMock()
    mock_update.effective_user.id = 12345
    mock_update.effective_chat.id = 12345
    mock_query = MagicMock()
    mock_query.data = "stop:555"
    mock_query.answer = AsyncMock()
    mock_query.edit_message_reply_markup = AsyncMock()
    mock_update.callback_query = mock_query

    await connector._handle_callback_query(mock_update, MagicMock())

    streamer.stop.assert_called_once()
    mock_query.edit_message_reply_markup.assert_called_once_with(reply_markup=None)


@pytest.mark.asyncio
async def test_callback_query_expired_approval():
    """When an approval is no longer pending (handler returns False), it shows an alert and marks expired."""
    connector = TelegramConnector(
        bot_token="fake:token", allowed_user_ids={"12345"}, default_chat_id="12345"
    )
    # Handler returns False indicating the approval is expired/not found
    connector.register_approval_handler(lambda resp: False)

    mock_update = MagicMock()
    mock_update.effective_user.id = 12345
    mock_update.effective_chat.id = 12345
    mock_query = MagicMock()
    mock_query.data = "approve:inc-expired"
    mock_query.message.message_id = 99
    mock_query.message.text = "🚨 [APPROVAL REQUIRED]\n\nAction: Restart deployment."
    mock_query.answer = AsyncMock()
    mock_query.edit_message_reply_markup = AsyncMock()
    mock_query.edit_message_text = AsyncMock()
    mock_update.callback_query = mock_query

    await connector._handle_callback_query(mock_update, MagicMock())

    mock_query.answer.assert_called_once_with(
        "⏱️ This approval request has expired or is no longer active.",
        show_alert=True,
    )
    mock_query.edit_message_reply_markup.assert_called_once_with(reply_markup=None)
    mock_query.edit_message_text.assert_called_once()
    assert "Expired" in mock_query.edit_message_text.call_args[0][0]
