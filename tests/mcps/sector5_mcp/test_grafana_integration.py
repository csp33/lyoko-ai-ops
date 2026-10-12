from unittest.mock import patch

import pytest
from sector5_mcp.config import GatewaySettings
from sector5_mcp.domain.models.upstream import UpstreamType
from sector5_mcp.server import build_gateway_application


def test_upstream_type_grafana_enum():
    assert UpstreamType.GRAFANA == "grafana"
    assert "grafana" in [u.value for u in UpstreamType]


def test_gateway_settings_grafana_defaults(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("GRAFANA_TOKEN", raising=False)
    settings = GatewaySettings(_env_file=None)
    assert settings.grafana_enabled is True
    assert "grafana" in settings.grafana_command.lower()
    assert "grafana" in settings.grafana_url
    assert settings.grafana_token == ""


def test_build_gateway_application_includes_grafana_when_configured():
    with patch("sector5_mcp.server.settings") as mock_settings:
        mock_settings.ha_enabled = False
        mock_settings.unifi_enabled = False
        mock_settings.k8s_enabled = False
        mock_settings.grafana_enabled = True
        mock_settings.grafana_url = "http://grafana:3000"
        mock_settings.grafana_token = "glsa_test_token_12345"
        mock_settings.grafana_command = "npx -y @grafana/mcp-server@latest"
        mock_settings.auth_enabled = False
        mock_settings.allowed_tools = ["*"]
        mock_settings.blocked_tools = []
        mock_settings.allowed_exec_commands = []
        mock_settings.blocked_exec_patterns = []
        mock_settings.blocked_namespaces = ["kube-system"]
        mock_settings.google_client_id = ""
        mock_settings.google_client_secret = ""
        mock_settings.base_url = "http://localhost:8080"
        mock_settings.redirect_path = "/oauth/callback"
        mock_settings.allowed_google_emails = []
        mock_settings.service_token = ""

        service, _ = build_gateway_application()

        assert UpstreamType.GRAFANA in service.upstreams
        client = service.upstreams[UpstreamType.GRAFANA]
        assert client.upstream_type == UpstreamType.GRAFANA
        assert client.prefix == "grafana_"
        assert client.env.get("GRAFANA_URL") == "http://grafana:3000"
        assert client.env.get("GRAFANA_SERVICE_ACCOUNT_TOKEN") == "glsa_test_token_12345"
