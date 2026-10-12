"""Cost estimation and token usage extraction for LLM model calls."""

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ModelPricing:
    """Pricing rates in USD per 1,000,000 tokens."""

    input_usd_per_1m: float
    output_usd_per_1m: float


# Default pricing catalog (USD per 1M tokens)
DEFAULT_MODEL_PRICING: dict[str, ModelPricing] = {
    # Fast / lightweight models
    "gpt-4o-mini": ModelPricing(input_usd_per_1m=0.15, output_usd_per_1m=0.60),
    "o3-mini": ModelPricing(input_usd_per_1m=1.10, output_usd_per_1m=4.40),
    "o1-mini": ModelPricing(input_usd_per_1m=1.10, output_usd_per_1m=4.40),
    # Standard & reasoning models
    "gpt-4o": ModelPricing(input_usd_per_1m=2.50, output_usd_per_1m=10.00),
    "gpt-4.1": ModelPricing(input_usd_per_1m=2.50, output_usd_per_1m=10.00),
    "o1": ModelPricing(input_usd_per_1m=15.00, output_usd_per_1m=60.00),
    "o3": ModelPricing(input_usd_per_1m=15.00, output_usd_per_1m=60.00),
}

DEFAULT_FALLBACK_PRICING = ModelPricing(input_usd_per_1m=1.50, output_usd_per_1m=6.00)


class ModelCostEstimator:
    """Calculates USD dollar costs based on a declarative pricing dictionary."""

    def __init__(
        self,
        pricing_table: dict[str, ModelPricing] | None = None,
        fallback_pricing: ModelPricing | None = None,
    ) -> None:
        self._pricing_table = dict(pricing_table or DEFAULT_MODEL_PRICING)
        self._fallback_pricing = fallback_pricing or DEFAULT_FALLBACK_PRICING

    def _resolve_pricing(self, model_name: str) -> ModelPricing:
        """Resolve pricing tier by exact match or longest matching prefix/substring."""
        normalized = model_name.lower().strip()
        if normalized in self._pricing_table:
            return self._pricing_table[normalized]

        # Match longest prefix or substring key (e.g. 'gpt-4o-mini-2024-07-18' -> 'gpt-4o-mini')
        for key in sorted(self._pricing_table.keys(), key=len, reverse=True):
            if key in normalized:
                return self._pricing_table[key]

        return self._fallback_pricing

    def estimate_cost_usd(self, model_name: str, input_tokens: int, output_tokens: int) -> float:
        """Estimate USD cost based on token pricing table."""
        pricing = self._resolve_pricing(model_name)
        return (
            input_tokens * pricing.input_usd_per_1m + output_tokens * pricing.output_usd_per_1m
        ) / 1_000_000

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
