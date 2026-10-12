"""Domain models and alias mappings for upstream MCP integrations."""

UPSTREAM_ALIASES: dict[str, str] = {
    # UniFi / Network
    "network": "unifi",
    "networking": "unifi",
    "wifi": "unifi",
    "router": "unifi",
    "switch": "unifi",
    "ap": "unifi",
    # Kubernetes
    "k8s": "kubernetes",
    "kube": "kubernetes",
    "cluster": "kubernetes",
    "pod": "kubernetes",
    "workload": "kubernetes",
    # Home Assistant
    "ha": "homeassistant",
    "hass": "homeassistant",
    "iot": "homeassistant",
    "smart_home": "homeassistant",
    "domotica": "homeassistant",
    # Grafana / Prometheus
    "grafana": "grafana",
    "prometheus": "grafana",
    "metrics": "grafana",
    "monitoring": "grafana",
    "alerts": "grafana",
    # GitHub
    "gh": "github",
    "git": "github",
    "repo": "github",
    # Telegram notifications
    "telegram": "telegram",
    "notify": "telegram",
    "notifications": "telegram",
    # Argo CD / GitOps live as Kubernetes CRDs (argoproj.io)
    "gitops": "kubernetes",
    "argocd": "kubernetes",
    "argo": "kubernetes",
    # Langfuse / LLM Observability
    "langfuse": "langfuse",
    "llm_observability": "langfuse",
    "traces": "langfuse",
}


def resolve_canonical_domain(domain: str) -> str:
    """Map a raw domain string or alias to its canonical upstream identifier."""
    raw = domain.strip().lower()
    return UPSTREAM_ALIASES.get(raw, raw)
