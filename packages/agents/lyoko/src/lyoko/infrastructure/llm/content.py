"""Extraction and normalization of message text from LLM responses."""

import ast
import json
import logging
from typing import Any

logger = logging.getLogger("lyoko.infrastructure.llm.content")


def extract_message_text(content: Any) -> str:
    """Extract plain text string from LLM response content.

    Different LLM providers and LangChain wrappers return ``AIMessage.content``
    as either a plain string, a list of strings, or a list of content block dictionaries/objects
    (such as Anthropic Claude format: ``[{'type': 'text', 'text': '...'}]``).
    If ``str(response.content)`` is used directly, Python stringifies the list representation,
    leaking Python syntax (brackets, quotes, dict keys) into user-facing outputs.

    This function safely normalizes any content representation into clean text.
    """
    if content is None:
        return ""

    if isinstance(content, str):
        stripped = content.strip()
        # Handle cases where content was already stringified as a Python list or JSON list representation
        if (
            stripped.startswith("[")
            and stripped.endswith("]")
            and ("'text'" in stripped or '"text"' in stripped)
        ):
            try:
                parsed = json.loads(stripped)
                if isinstance(parsed, list):
                    return extract_message_text(parsed)
            except Exception:
                try:
                    parsed = ast.literal_eval(stripped)
                    if isinstance(parsed, list):
                        return extract_message_text(parsed)
                except Exception:
                    pass
        return content

    if isinstance(content, list):
        text_parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                text_parts.append(item)
            elif isinstance(item, dict):
                if item.get("type") == "text" or "text" in item:
                    text_parts.append(str(item.get("text", "")))
                elif "content" in item:
                    text_parts.append(extract_message_text(item["content"]))
            elif hasattr(item, "text"):
                text_parts.append(str(item.text))
            elif hasattr(item, "content"):
                text_parts.append(extract_message_text(item.content))
            else:
                text_parts.append(str(item))
        return "".join(text_parts) if text_parts else str(content)

    if hasattr(content, "text"):
        return str(content.text)

    return str(content)


def extract_token_usage(response: Any) -> tuple[int, int]:
    """Extract input and output tokens from model response metadata."""
    usage_meta = getattr(response, "usage_metadata", None) or {}
    if usage_meta:
        return (
            int(usage_meta.get("input_tokens", 0)),
            int(usage_meta.get("output_tokens", 0)),
        )
    resp_meta = getattr(response, "response_metadata", None) or {}
    token_usage = resp_meta.get("token_usage", {})
    return (
        int(token_usage.get("prompt_tokens", 0)),
        int(token_usage.get("completion_tokens", 0)),
    )
