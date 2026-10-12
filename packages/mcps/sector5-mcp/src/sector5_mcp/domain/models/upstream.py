"""Upstream MCP domain models."""

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class UpstreamType(StrEnum):
    HOME_ASSISTANT = "homeassistant"
    UNIFI = "unifi"
    KUBERNETES = "kubernetes"
    GRAFANA = "grafana"
    GITHUB = "github"
    TELEGRAM = "telegram"
    LANGFUSE = "langfuse"


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    parameters: dict[str, Any] = field(default_factory=dict)
    upstream_type: UpstreamType | str = "unknown"
    annotations: dict[str, Any] = field(default_factory=dict)

    @property
    def read_only(self) -> bool | None:
        """The upstream's explicit read-only hint, or ``None`` when it declared none.

        MCP tool annotations are optional hints. ``None`` means the upstream said nothing, so
        callers must fall back to their own policy instead of assuming the tool is safe.
        """
        for key in ("readOnlyHint", "read_only_hint"):
            if key in self.annotations:
                return bool(self.annotations[key])
        return None


@dataclass(frozen=True)
class ToolResult:
    status: str
    content: Any
    is_error: bool = False
