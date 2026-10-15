"""Unit tests for markdown prompt loading and prompt integrity."""

import pytest
from lyoko.application.prompts.chat import CHAT_SYSTEM_PROMPT
from lyoko.application.prompts.incident import RESOLVE_SYSTEM_PROMPT
from lyoko.application.prompts.loader import load_prompt
from lyoko.application.prompts.router import ROUTER_SYSTEM_PROMPT


def test_load_prompt_success():
    chat_prompt = load_prompt("chat.md")
    assert "LYOKO" in chat_prompt
    assert "AIOps" in chat_prompt


def test_load_prompt_missing_file():
    with pytest.raises(FileNotFoundError, match="Prompt file not found"):
        load_prompt("nonexistent_prompt.md")


def test_all_prompts_loaded_properly():
    assert "LYOKO" in CHAT_SYSTEM_PROMPT
    assert "INCIDENT" in ROUTER_SYSTEM_PROMPT
    assert "autonomous" in RESOLVE_SYSTEM_PROMPT.lower() or "lyoko" in RESOLVE_SYSTEM_PROMPT.lower()
