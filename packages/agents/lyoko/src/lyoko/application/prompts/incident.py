"""System prompt provider for incident agent phases."""

from lyoko.application.context.loader import build_agent_context
from lyoko.application.prompts.loader import load_prompt


class IncidentPromptProvider:
    """Provides system prompts for the incident investigation and remediation phases."""

    @staticmethod
    def get_diagnose_prompt() -> str:
        return f"{load_prompt('diagnose.md')}\n\n{build_agent_context('diagnose')}"

    @staticmethod
    def get_remediate_prompt() -> str:
        return f"{load_prompt('remediate.md')}\n\n{build_agent_context('remediate')}"

    @staticmethod
    def get_verify_prompt() -> str:
        return f"{load_prompt('verify.md')}\n\n{build_agent_context('verify')}"

    @staticmethod
    def get_resolve_prompt() -> str:
        return f"{load_prompt('resolve.md')}\n\n{build_agent_context('supervisor')}"


DIAGNOSE_SYSTEM_PROMPT = IncidentPromptProvider.get_diagnose_prompt()
REMEDIATE_SYSTEM_PROMPT = IncidentPromptProvider.get_remediate_prompt()
VERIFY_SYSTEM_PROMPT = IncidentPromptProvider.get_verify_prompt()
RESOLVE_SYSTEM_PROMPT = IncidentPromptProvider.get_resolve_prompt()
