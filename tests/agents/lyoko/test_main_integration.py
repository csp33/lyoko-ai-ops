"""Integration tests for LYOKO application composition root and FastAPI lifespan."""

from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from lyoko.application.safety.hitl.manager import ApprovalManager
from lyoko.composition import build_chat_manager
from lyoko.infrastructure.api.server import create_app


@pytest.mark.asyncio
async def test_app_health_endpoint():
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp_healthz = await client.get("/healthz")
        assert resp_healthz.status_code == 200
        assert resp_healthz.json()["status"] == "healthy"
        assert resp_healthz.json()["service"] == "lyoko-agent"

        resp_health = await client.get("/health")
        assert resp_health.status_code == 200
        assert resp_health.json()["status"] == "healthy"
        assert resp_health.json()["service"] == "lyoko-agent"


@pytest.mark.asyncio
async def test_build_chat_manager_disabled(monkeypatch):
    monkeypatch.setattr("lyoko.config.settings.telegram_enabled", False)
    approval_mgr = ApprovalManager()
    mgr = build_chat_manager(approval_mgr)
    assert len(mgr.connectors) == 0


@pytest.mark.asyncio
async def test_build_chat_manager_enabled(monkeypatch):
    monkeypatch.setattr("lyoko.config.settings.telegram_enabled", True)
    monkeypatch.setattr("lyoko.config.settings.telegram_bot_token", "123456:fake_token")
    monkeypatch.setattr("lyoko.config.settings.telegram_allowed_user_ids", "111,222")
    monkeypatch.setattr("lyoko.config.settings.telegram_default_chat_id", "111")

    approval_mgr = ApprovalManager()
    mgr = build_chat_manager(approval_mgr)
    assert len(mgr.connectors) == 1
    assert mgr.connectors[0].is_user_authorized("111") is True
    assert mgr.connectors[0].is_user_authorized("999") is False


@pytest.mark.asyncio
async def test_app_lifespan_lifecycle():
    with (
        patch(
            "lyoko.infrastructure.chat.manager.ChatManager.start_all", new_callable=AsyncMock
        ) as mock_start,
        patch(
            "lyoko.infrastructure.chat.manager.ChatManager.stop_all", new_callable=AsyncMock
        ) as mock_stop,
    ):
        app = create_app()
        async with app.router.lifespan_context(app):
            pass

        mock_start.assert_called_once()
        mock_stop.assert_called_once()


@pytest.mark.asyncio
async def test_webhook_and_chat_use_the_graph_stored_in_app_state(monkeypatch):
    """Lifespan replaces the graph (to add the checkpointer); every entry point must follow."""
    from unittest.mock import MagicMock

    from lyoko.domain.models.chat import ChatUser, IncomingMessage

    monkeypatch.setattr("lyoko.config.settings.alert_debounce_seconds", 0.0)
    monkeypatch.setattr("lyoko.config.settings.alert_dedup_cooldown_seconds", 0)

    app = create_app()
    replacement = MagicMock()
    replacement.ainvoke = AsyncMock(return_value={"reply": "from the replacement graph"})
    app.state.workflow_engine = replacement

    alert = {"alerts": [{"status": "firing", "labels": {"alertname": "HighLatency"}}]}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/webhook/alertmanager", json=alert)

    assert resp.status_code == 200
    replacement.ainvoke.assert_awaited_once()
    assert replacement.ainvoke.call_args.args[0]["event_type"] == "alert"

    replacement.ainvoke.reset_mock()
    chat_manager = app.state.chat_manager
    assert chat_manager.session_tracker is not None
    from lyoko.application.chat.agent import InteractiveChatAgent
    from lyoko.composition import wire_chat_agent

    class _Connector:
        handlers: list = []

        def register_message_handler(self, handler):
            self.handlers.append(handler)

    connector = _Connector()
    chat_manager.connectors.append(connector)
    wire_chat_agent(app, chat_manager)
    (handler,) = connector.handlers
    assert isinstance(handler.__self__, InteractiveChatAgent)

    reply = await handler(
        IncomingMessage(
            message_id="1",
            chat_id="42",
            user=ChatUser(user_id="42", username="admin"),
            text="hello",
        )
    )
    assert reply == "from the replacement graph"
    assert replacement.ainvoke.call_args.args[0]["event_type"] == "message"
