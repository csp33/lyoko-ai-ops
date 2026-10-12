"""Cost estimation and token usage extraction for LLM model calls."""

from typing import Any


class ModelCostEstimator:
    """Calculates USD dollar costs based on model pricing tiers and extracts token usage."""

    def estimate_cost_usd(self, model_name: str, input_tokens: int, output_tokens: int) -> float:
        """Estimate USD cost based on known OpenAI pricing tiers."""
        lowered = model_name.lower()
        if "mini" in lowered:
            return (input_tokens * 0.15 + output_tokens * 0.60) / 1_000_000
        if "4o" in lowered or "4.1" in lowered:
            return (input_tokens * 2.50 + output_tokens * 10.00) / 1_000_000
        return (input_tokens * 1.50 + output_tokens * 6.00) / 1_000_000

    def extract_usage(self, response: Any) -> tuple[int, int]:
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
