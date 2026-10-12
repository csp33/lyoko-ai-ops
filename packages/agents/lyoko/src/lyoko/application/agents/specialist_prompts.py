"""Specialist domain prompts for LYOKO loaded from markdown files."""

from lyoko.application.context.loader import build_agent_context
from lyoko.application.prompts.loader import load_prompt


class SpecialistPromptProvider:
    """Provides prompt templates and architectural context for domain specialists."""

    @staticmethod
    def get_prompt(domain: str) -> str:
        domain_normalized = domain.strip().lower()
        mapping = {
            "network": "specialists/network.md",
            "unifi": "specialists/network.md",
            "k8s": "specialists/k8s.md",
            "kubernetes": "specialists/k8s.md",
            "smarthome": "specialists/smarthome.md",
            "homeassistant": "specialists/smarthome.md",
            "observability": "specialists/observability.md",
            "grafana": "specialists/observability.md",
        }
        template_name = mapping.get(domain_normalized, f"specialists/{domain_normalized}.md")
        return f"{load_prompt(template_name)}\n\n{build_agent_context(domain_normalized)}"
