"""Domain models for guardrails and security policies."""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class GuardrailPolicy:
    """Security guardrail rules for tool filtering and execution boundaries."""

    allowed_tools: list[str] = field(default_factory=lambda: ["*"])
    blocked_tools: list[str] = field(default_factory=list)
    allowed_exec_commands: list[str] = field(default_factory=list)
    blocked_exec_patterns: list[str] = field(
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
        ]
    )
    blocked_namespaces: list[str] = field(
        default_factory=lambda: ["kube-system", "kube-public", "kube-node-lease"]
    )
    # Kubernetes tools matching these glob patterns only read state. In a protected namespace,
    # every other Kubernetes tool is treated as a mutation, so unknown tools fail closed.
    read_only_tools: list[str] = field(
        default_factory=lambda: [
            "*_get",
            "*_get_*",
            "*.get_*",
            "*.get",
            "*_list",
            "*_list_*",
            "*.list_*",
            "*.list",
            "*_log",
            "*_logs",
            "*.log",
            "*.logs",
            "*_top",
            "*.top",
            "*_stats_summary",
            "configuration_view",
        ]
    )
    allowed_github_repos: list[str] = field(default_factory=lambda: ["*"])
    blocked_github_repos: list[str] = field(default_factory=list)
