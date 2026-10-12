"""Configuration settings for LYOKO agent."""

import json
from typing import Any
from urllib.parse import quote_plus

from pydantic import AliasChoices, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from lyoko.domain.models.guardrail import DEFAULT_READ_ONLY_TOOLS


class AgentSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Agent Server settings
    lyoko_host: str = Field(default="0.0.0.0", description="Host to bind FastAPI server")
    lyoko_port: int = Field(default=9000, description="Port for FastAPI webhook receiver")

    # LLM Provider
    openai_api_key: str = Field(default="", description="OpenAI API Key")
    openai_model: str = Field(
        default="gpt-4o-mini",
        description="OpenAI model for chat, remediation, verification, and domain specialists.",
    )
    openai_diagnose_model: str = Field(
        default="gpt-4.1",
        description=(
            "OpenAI model for the diagnose phase, which synthesizes the specialists' evidence and "
            "decides whether the incident is actionable. A stronger model here pays off because "
            "mis-synthesis is the main failure mode. Empty falls back to openai_model."
        ),
    )
    openai_use_responses_api: bool = Field(
        default=True,
        description=(
            "Use the OpenAI Responses API (/v1/responses) instead of Chat Completions. It keeps "
            "reasoning and tool context across steps and uses prompt caching better. Set false to "
            "fall back to Chat Completions."
        ),
    )

    # MCP Gateway connection
    mcp_server_url: str = Field(
        default="http://localhost:8000/mcp", description="sector5-mcp Streamable HTTP URL"
    )
    service_token: str = Field(
        default="", description="Bearer token to authenticate against sector5-mcp"
    )
    mcp_fail_fast: bool = Field(
        default=True,
        description=(
            "Abort startup when the MCP gateway rejects our credentials or the URL is not an "
            "MCP endpoint (HTTP 401/403/404). A merely unreachable gateway only logs an error."
        ),
    )

    # Telegram Bot settings
    telegram_enabled: bool = Field(
        default=False,
        description="Enable Telegram private assistant, channel, and HITL notifications",
    )
    telegram_bot_token: SecretStr | None = Field(
        default=None, description="Telegram Bot Token from @BotFather"
    )
    telegram_allowed_user_ids: list[str] | str = Field(
        default_factory=list,
        description="List of authorized Telegram user IDs allowed to interact with the bot",
    )
    telegram_allowed_chat_ids: list[str] | str = Field(
        default_factory=list,
        description="List of authorized Telegram channel or group chat IDs (e.g. -1001234567890)",
    )
    telegram_default_chat_id: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "telegram_default_chat_id", "telegram_chat_id", "telegram_channel_id"
        ),
        description="Default Telegram Chat/Channel ID for broadcast notifications and alerts",
    )
    telegram_discussion_group_id: str | None = Field(
        default=None,
        validation_alias=AliasChoices("telegram_discussion_group_id", "discussion_group_id"),
        description="Telegram Discussion Group / Supergroup ID linked to the broadcast channel",
    )

    # Guardrails
    max_remediation_retries: int = Field(
        default=2, description="Maximum automated remediation retry loops"
    )
    verification_delay_seconds: int = Field(
        default=10, description="Seconds to wait before verifying pod health"
    )
    shutdown_drain_timeout_seconds: float = Field(
        default=25.0,
        ge=0.0,
        description="Seconds to wait for in-flight incident tasks to drain during graceful shutdown",
    )
    read_only_tools: list[str] | str = Field(
        default_factory=lambda: list(DEFAULT_READ_ONLY_TOOLS),
        description=(
            "Glob patterns of gateway tools the incident agent may call freely because they only "
            "inspect state. Any tool not matching here or in AUTO_APPROVED_TOOLS needs approval."
        ),
    )
    auto_approved_tools: list[str] | str = Field(
        default_factory=list,
        description=(
            "Glob patterns of state-changing gateway tools the incident agent may call without "
            "human approval (e.g. 'resources_scale'). Empty means every change needs approval."
        ),
    )
    max_agent_steps: int = Field(
        default=15,
        ge=1,
        description="Maximum tool-use iterations of each incident investigation or remediation",
    )
    max_supervisor_steps: int = Field(
        default=5,
        ge=1,
        description="Maximum supervisor delegation iterations per phase",
    )
    max_tool_calls_per_run: int = Field(
        default=20,
        ge=1,
        description=(
            "Maximum tool calls a single agent run may execute before it must summarize. "
            "Bounds fan-out across large resource lists so one run cannot balloon the context."
        ),
    )
    max_tool_output_chars: int = Field(
        default=8000,
        ge=100,
        description=(
            "Maximum characters of a single tool result kept in the agent context. Longer "
            "results are truncated with a note to protect the token budget."
        ),
    )

    # Alert Storm Protection & Cascade Trace Prevention
    alert_debounce_seconds: float = Field(
        default=10.0,
        ge=0.0,
        description="Seconds to debounce and aggregate incoming alerts before launching an incident",
    )
    alert_storm_threshold: int = Field(
        default=8,
        ge=1,
        description="Number of alerts in window required to trip the alert storm circuit breaker",
    )
    alert_storm_window_seconds: int = Field(
        default=60,
        ge=1,
        description="Sliding window in seconds to monitor alert velocity for storm detection",
    )
    alert_storm_cooldown_seconds: int = Field(
        default=120,
        ge=1,
        description="Seconds to keep the circuit breaker open before attempting recovery",
    )
    alert_dedup_cooldown_seconds: int = Field(
        default=300,
        ge=0,
        description="Seconds to suppress duplicate alerts for recently resolved/escalated incidents",
    )
    max_concurrent_incidents: int = Field(
        default=2,
        ge=1,
        description="Maximum concurrent LangGraph incident investigation workflows allowed",
    )
    memory_similarity_threshold: float = Field(
        default=0.2,
        ge=0.0,
        le=1.0,
        description="Minimum cosine similarity required to inject memories into agent context",
    )
    chat_memory_similarity_threshold: float = Field(
        default=0.35,
        ge=0.0,
        le=1.0,
        description=(
            "Similarity floor for injecting memories into interactive chat. Chat prompts are "
            "short and generic, so a very low threshold surfaces loosely related rules that can "
            "hijack the operator's actual request. It must still stay below the similarity of a "
            "genuinely relevant short operator rule (empirically ~0.45), or real memories are "
            "silently dropped."
        ),
    )

    # General Environment
    environment: str = Field(
        default="local",
        description="Deployment environment (e.g. 'local', 'homelab', 'development', 'production')",
    )

    # Observability (Langfuse / OpenTelemetry)
    langfuse_enabled: bool = Field(default=True, description="Enable Langfuse tracing")
    langfuse_public_key: str | None = Field(default=None, description="Langfuse Public Key")
    langfuse_secret_key: SecretStr | None = Field(default=None, description="Langfuse Secret Key")
    langfuse_host: str = Field(
        default="https://cloud.langfuse.com", description="Langfuse Host URL"
    )
    langfuse_environment: str | None = Field(
        default=None,
        description="Langfuse environment tag override (defaults to environment setting if not set)",
    )
    langfuse_release: str | None = Field(
        default=None,
        description="Release version identifier for Langfuse tracing",
    )

    chat_session_idle_timeout_seconds: int = Field(
        default=900,
        ge=1,
        description=(
            "Seconds of chat inactivity after which a new Langfuse session is started "
            "for the next message in the same chat (users can also send /new)"
        ),
    )

    # PostgreSQL Checkpointer (Persistent LangGraph state)
    postgres_uri: str | None = Field(
        default=None,
        description="Full PostgreSQL connection URI (e.g., postgresql://user:pass@host:5432/dbname)",
    )
    postgres_host: str = Field(
        default="postgresql-rw.postgresql-cnpg.svc.cluster.local",
        description="PostgreSQL hostname",
    )
    postgres_port: int = Field(default=5432, description="PostgreSQL port")
    postgres_user: str = Field(default="lyoko", description="PostgreSQL username")
    postgres_password: str = Field(default="", description="PostgreSQL password")
    postgres_db: str = Field(default="lyoko", description="PostgreSQL database name")
    postgres_pool_max_size: int = Field(
        default=20, description="PostgreSQL connection pool max size"
    )

    @field_validator(
        "telegram_allowed_user_ids",
        "telegram_allowed_chat_ids",
        "read_only_tools",
        "auto_approved_tools",
        mode="before",
    )
    @classmethod
    def parse_allowed_ids(cls, v: Any) -> list[str]:
        if isinstance(v, (int, float)):
            return [str(v)]
        if isinstance(v, str):
            v = v.strip()
            if v.startswith("[") and v.endswith("]"):
                try:
                    return [str(x) for x in json.loads(v)]
                except Exception:
                    pass
            return [item.strip() for item in v.split(",") if item.strip()]
        if isinstance(v, (list, tuple, set)):
            return [str(x) for x in v]
        return v or []

    def get_langfuse_environment(self) -> str:
        """Return the effective Langfuse environment string."""
        return self.langfuse_environment or self.environment

    def get_postgres_uri(self) -> str | None:
        """Construct PostgreSQL connection URI if configured."""
        if self.postgres_uri:
            return self.postgres_uri
        if self.postgres_password:
            user = quote_plus(self.postgres_user)
            password = quote_plus(self.postgres_password)
            return (
                f"postgresql://{user}:{password}"
                f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
            )
        return None


settings = AgentSettings()
