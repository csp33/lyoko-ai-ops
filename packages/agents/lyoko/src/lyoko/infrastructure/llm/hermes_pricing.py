"""Pricing estimation, token usage, and status helpers for Hermes harness."""

from typing import Any

_ERROR_MARKERS = (
    "'is_error': true",
    '"is_error": true',
    '"is_error":true',
    "error executing tool",
    "not found on any upstream",
    "is forbidden:",
    "permission denied",
)


def is_tool_error(output: str) -> bool:
    """Check if tool output string contains common failure markers."""
    lowered = output.lower()
    return any(marker in lowered for marker in _ERROR_MARKERS)


def format_tool_status(
    tool_call: dict[str, Any], turns_used: int, max_turns: int, cost_usd: float
) -> str:
    """Format live status indicator for Telegram message drafts."""
    name = str(tool_call.get("name") or "tool")
    args = tool_call.get("args") or {}
    step_info = f"(Step {turns_used + 1}/{max_turns} • ${cost_usd:.3f})"
    if name.startswith("ask_") and name.endswith("_specialist"):
        return f"🧩 Consulting {name[4:-11]} specialist {step_info}"
    if name == "gateway_call_tool":
        return f"🛰️ Calling <code>{args.get('tool_name', 'tool')}</code> {step_info}"
    if name == "gateway_get_tool_schema":
        return f"🔍 Reading <code>{args.get('tool_name', 'tool')}</code> schema {step_info}"
    return f"🛰️ Calling <code>{name}</code> {step_info}"


def estimate_cost_usd(model_name: str, input_tokens: int, output_tokens: int) -> float:
    """Estimate USD cost based on known OpenAI pricing tiers."""
    lowered = model_name.lower()
    if "mini" in lowered:
        return (input_tokens * 0.15 + output_tokens * 0.60) / 1_000_000
    if "4o" in lowered or "4.1" in lowered:
        return (input_tokens * 2.50 + output_tokens * 10.00) / 1_000_000
    return (input_tokens * 1.50 + output_tokens * 6.00) / 1_000_000


def extract_usage(response: Any) -> tuple[int, int]:
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
