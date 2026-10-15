"""System prompt provider for the conversational branch."""

from lyoko.application.prompts.loader import load_prompt


class ChatPromptProvider:
    """Provides system prompts for the conversational chat branch."""

    @staticmethod
    def get_prompt() -> str:
        return load_prompt("chat.md")


CHAT_SYSTEM_PROMPT = ChatPromptProvider.get_prompt()
