"""Configuration and environment settings for sector5-mcp Gateway."""

import json
from typing import Any

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class GatewaySettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Server settings
    mcp_host: str = Field(default="0.0.0.0", description="Host to bind FastMCP Gateway")
    mcp_port: int = Field(default=8000, description="Port for FastMCP Gateway server")
    mcp_transport: str = Field(
        default="http", description="Transport type: 'http' (Streamable HTTP on /mcp) or 'stdio'"
    )

    # Authentication settings
    auth_enabled: bool = Field(
        default=False, description="Enable Google Auth / Service token verification"
    )
    google_client_id: str = Field(
        default="", description="Google OAuth2 Client ID for ID token validation"
    )
    google_client_secret: str = Field(
        default="", description="Google OAuth2 Client Secret for OIDC Proxy and DCR"
    )
    base_url: str = Field(
        default="http://localhost:8080",
        description="Base URL of the MCP Gateway for OAuth/DCR metadata (e.g. https://mcp.internal.cspaez.org)",
    )
    redirect_path: str = Field(
        default="/oauth/callback",
        description="OAuth callback path configured in Google Cloud Console",
    )
    allowed_google_emails: list[str] | str = Field(
        default_factory=list,
        description="Allowed Google email addresses (e.g. ['admin@gmail.com'] or 'admin@gmail.com')",
    )
    service_token: str = Field(
        default="", description="Internal static bearer token for automated in-cluster agents"
    )
    jwt_secret: str = Field(
        default="", description="Secret key for signing and verifying HS256 JWT tokens"
    )

    # Upstream: Home Assistant MCP (github.com/homeassistant-ai/ha-mcp)
    ha_enabled: bool = Field(default=True, description="Enable Home Assistant MCP upstream")
    ha_command: str = Field(default="ha-mcp", description="Command to launch ha-mcp")
    hass_url: str = Field(
        default="http://homeassistant.default.svc.cluster.local:8123",
        description="Home Assistant URL",
    )
    hass_token: str = Field(default="", description="Home Assistant Long-Lived Access Token")

    # Upstream: UniFi Network MCP (github.com/sirkirby/unifi-mcp)
    unifi_enabled: bool = Field(default=True, description="Enable UniFi Network MCP upstream")
    unifi_command: str = Field(
        default="unifi-network-mcp", description="Command to launch unifi-network-mcp"
    )
    unifi_url: str = Field(default="https://192.168.33.1", description="UniFi Controller Base URL")
    unifi_user: str = Field(default="", description="UniFi username")
    unifi_password: str = Field(default="", description="UniFi password")
    unifi_site: str = Field(default="default", description="UniFi site name")
    unifi_verify_ssl: bool = Field(
        default=False, description="Verify SSL certificate for UniFi controller"
    )

    # Upstream: Kubernetes (native kubectl adapter)
    k8s_enabled: bool = Field(default=True, description="Enable Kubernetes upstream")
    k8s_command: str = Field(default="kubectl", description="Command to execute kubectl")
    kubeconfig_path: str | None = Field(
        default=None, description="Path to kubeconfig file if not default"
    )

    # Upstream: Grafana MCP (github.com/grafana/mcp-grafana)
    grafana_enabled: bool = Field(default=True, description="Enable Grafana MCP upstream")
    grafana_command: str = Field(
        default="npx -y mcp-grafana-npx", description="Command to launch grafana mcp"
    )
    grafana_url: str = Field(
        default="http://grafana.monitoring.svc.cluster.local:3000",
        description="Grafana Controller Base URL",
    )
    grafana_token: str = Field(default="", description="Grafana Service Account or API Token")

    # Upstream: GitHub MCP (github.com/github/github-mcp-server or modelcontextprotocol/server-github)
    github_enabled: bool = Field(default=True, description="Enable GitHub MCP upstream")
    github_command: str = Field(
        default="npx -y @modelcontextprotocol/server-github",
        description="Command to launch GitHub MCP server",
    )
    github_token: str = Field(default="", description="GitHub Personal Access Token")
    github_owner: str = Field(default="", description="Default GitHub owner or organization")
    github_allowed_repos: list[str] | str = Field(
        default_factory=lambda: ["*"],
        description="Allowed repositories glob patterns (e.g. ['*'] or ['csp33/*', 'org/repo'])",
    )
    github_blocked_repos: list[str] | str = Field(
        default_factory=list,
        description="Blocked repositories glob patterns (e.g. ['*/secrets-*'])",
    )

    # Upstream: Langfuse MCP (https://cloud.langfuse.com/api/public/mcp)
    langfuse_enabled: bool = Field(default=False, description="Enable Langfuse MCP upstream")
    langfuse_url: str = Field(
        default="https://cloud.langfuse.com/api/public/mcp",
        description="Langfuse MCP endpoint URL",
    )
    langfuse_public_key: str = Field(default="", description="Langfuse Public Key for MCP")
    langfuse_secret_key: SecretStr | None = Field(
        default=None, description="Langfuse Secret Key for MCP"
    )

    # Telegram Bot settings
    telegram_enabled: bool = Field(
        default=False, description="Enable Telegram notifications and alerts"
    )
    telegram_bot_token: SecretStr | None = Field(
        default=None, description="Telegram Bot Token from @BotFather"
    )
    telegram_default_chat_id: str | None = Field(
        default=None, description="Default Telegram Chat ID for alerts and messages"
    )

    # Security & Guardrails
    allowed_tools: list[str] | str = Field(
        default_factory=lambda: ["*"],
        description="Glob patterns of permitted tool names (e.g. ['*'] or ['pods_*', 'ha_*'])",
    )
    blocked_tools: list[str] | str = Field(
        default_factory=list,
        description="Glob patterns of strictly forbidden tool names (e.g. ['resources_delete'])",
    )
    allowed_exec_commands: list[str] | str = Field(
        default_factory=list,
        description="Allowed base binaries for container exec (empty means all non-blocked are allowed)",
    )
    blocked_exec_patterns: list[str] | str = Field(
        default_factory=lambda: [
            r"\brm\b",
            r"\brmdir\b",
            r"\bdd\b",
            r"\bmkfs\b",
            r"\bchmod\b",
            r"\bchown\b",
            r"\breboot\b",
            r"\bshutdown\b",
            r"\bpoweroff\b",
            r">\s*/dev/",
            r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:",
        ],
        description="Regex patterns prohibited in command execution arguments",
    )
    blocked_namespaces: list[str] | str = Field(
        default=["kube-system", "kube-public", "kube-node-lease"],
        description="Namespaces where mutations and exec are strictly prohibited",
    )

    @field_validator(
        "allowed_google_emails",
        "allowed_tools",
        "blocked_tools",
        "allowed_exec_commands",
        "blocked_namespaces",
        "github_allowed_repos",
        "github_blocked_repos",
        mode="before",
    )
    @classmethod
    def parse_str_or_json_list(cls, v: Any) -> list[str]:
        if isinstance(v, str):
            v = v.strip()
            if v.startswith("[") and v.endswith("]"):
                try:
                    return json.loads(v)
                except Exception:
                    pass
            return [item.strip() for item in v.split(",") if item.strip()]
        return v or []


settings = GatewaySettings()
