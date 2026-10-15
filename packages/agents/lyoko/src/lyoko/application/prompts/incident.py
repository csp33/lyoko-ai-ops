"""System prompt provider for incident agent resolution."""

from lyoko.application.prompts.loader import load_prompt


class IncidentPromptProvider:
    """Provides system prompts for the incident resolution phase."""

    @staticmethod
    def get_resolve_prompt() -> str:
        return load_prompt("resolve.md")


RESOLVE_SYSTEM_PROMPT = IncidentPromptProvider.get_resolve_prompt()
