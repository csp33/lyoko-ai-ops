import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from lyoko.composition import build_domain_specialists, build_llm_adapter, build_supervisor
from lyoko.config import settings
from lyoko.infrastructure.api.server import create_app, lifespan
from lyoko.logging_config import HealthEndpointFilter


@pytest.mark.asyncio
async def test_healthz_endpoint():
    app = create_app()
    async with lifespan(app):
        # Without postgres credentials, app state has no checkpointer
        assert app.state.workflow_engine is not None
        assert "kubernetes" in app.state.specialists
        assert "unifi" in app.state.specialists
        assert "homeassistant" in app.state.specialists
        assert "grafana" in app.state.specialists
        assert app.state.supervisor is not None


def test_build_domain_specialists_and_supervisor():
    specialists = build_domain_specialists(mcp_client=None, llm=None)
    assert set(specialists.keys()) == {"kubernetes", "unifi", "homeassistant", "grafana"}
    supervisor = build_supervisor(specialists, llm=None)
    assert supervisor.specialists == specialists


def test_build_llm_adapter_model_override(monkeypatch):
    monkeypatch.setattr(settings, "openai_api_key", "sk-test")
    assert build_llm_adapter().model_name == settings.openai_model
    assert build_llm_adapter().use_responses_api is True
    assert build_llm_adapter("gpt-4.1").model_name == "gpt-4.1"
    assert build_llm_adapter(settings.openai_diagnose_model).model_name == "gpt-4.1"
    assert build_llm_adapter(use_responses_api=False).use_responses_api is False


def test_create_app_wires_a_stronger_diagnose_model(monkeypatch):
    monkeypatch.setattr(settings, "openai_api_key", "sk-test")
    monkeypatch.setattr(settings, "openai_model", "gpt-4o-mini")
    monkeypatch.setattr(settings, "openai_diagnose_model", "gpt-4.1")

    app = create_app()

    assert app.state.llm.model_name == "gpt-4o-mini"
    assert app.state.diagnose_llm.model_name == "gpt-4.1"
    assert app.state.diagnose_supervisor.llm is app.state.diagnose_llm
    assert app.state.supervisor.llm is app.state.llm


@pytest.mark.asyncio
@patch("lyoko.infrastructure.api.server.AsyncPostgresSaver")
@patch("lyoko.infrastructure.api.server.AsyncConnectionPool")
async def test_lifespan_with_postgres_configured(mock_pool_cls, mock_saver_cls):
    mock_pool = MagicMock()
    mock_pool.open = AsyncMock()
    mock_pool.close = AsyncMock()
    mock_pool_cls.return_value = mock_pool

    mock_saver = MagicMock(spec=AsyncPostgresSaver)
    mock_saver.setup = AsyncMock()
    mock_saver_cls.return_value = mock_saver

    with patch("lyoko.infrastructure.api.server.settings.postgres_password", "testpassword"):
        app = create_app()
        async with lifespan(app):
            mock_pool.open.assert_awaited_once()
            mock_saver.setup.assert_awaited_once()
            assert app.state.checkpointer is mock_saver
            assert app.state.db_pool is mock_pool

        mock_pool.close.assert_awaited_once()


def test_health_endpoint_filter_args():
    filter_obj = HealthEndpointFilter()

    # /healthz and /health with uvicorn args should be filtered out
    record_healthz = logging.LogRecord(
        name="uvicorn.access",
        level=logging.INFO,
        pathname="",
        lineno=0,
        msg='%s - "%s %s HTTP/%s" %d',
        args=("127.0.0.1:50000", "GET", "/healthz", "1.1", 200),
        exc_info=None,
    )
    assert filter_obj.filter(record_healthz) is False

    record_health = logging.LogRecord(
        name="uvicorn.access",
        level=logging.INFO,
        pathname="",
        lineno=0,
        msg='%s - "%s %s HTTP/%s" %d',
        args=("127.0.0.1:50000", "GET", "/health", "1.1", 200),
        exc_info=None,
    )
    assert filter_obj.filter(record_health) is False

    record_health_query = logging.LogRecord(
        name="uvicorn.access",
        level=logging.INFO,
        pathname="",
        lineno=0,
        msg='%s - "%s %s HTTP/%s" %d',
        args=("127.0.0.1:50000", "GET", "/healthz?foo=bar", "1.1", 200),
        exc_info=None,
    )
    assert filter_obj.filter(record_health_query) is False

    # Other endpoints should NOT be filtered out
    record_other = logging.LogRecord(
        name="uvicorn.access",
        level=logging.INFO,
        pathname="",
        lineno=0,
        msg='%s - "%s %s HTTP/%s" %d',
        args=("127.0.0.1:50000", "POST", "/webhook/alertmanager", "1.1", 200),
        exc_info=None,
    )
    assert filter_obj.filter(record_other) is True


def test_health_endpoint_filter_message():
    filter_obj = HealthEndpointFilter()

    # Formatted messages without args
    record_healthz_msg = logging.LogRecord(
        name="uvicorn.access",
        level=logging.INFO,
        pathname="",
        lineno=0,
        msg='127.0.0.1:50000 - "GET /healthz HTTP/1.1" 200 OK',
        args=(),
        exc_info=None,
    )
    assert filter_obj.filter(record_healthz_msg) is False

    record_health_msg = logging.LogRecord(
        name="uvicorn.access",
        level=logging.INFO,
        pathname="",
        lineno=0,
        msg='127.0.0.1:50000 - "GET /health HTTP/1.1" 200 OK',
        args=(),
        exc_info=None,
    )
    assert filter_obj.filter(record_health_msg) is False

    record_other_msg = logging.LogRecord(
        name="uvicorn.access",
        level=logging.INFO,
        pathname="",
        lineno=0,
        msg='127.0.0.1:50000 - "POST /api/v1/feedback HTTP/1.1" 200 OK',
        args=(),
        exc_info=None,
    )
    assert filter_obj.filter(record_other_msg) is True
