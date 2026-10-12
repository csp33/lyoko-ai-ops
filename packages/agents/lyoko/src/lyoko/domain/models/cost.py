"""In-memory run cost registry for tracking real-time LLM token execution costs."""

from typing import ClassVar


class RunCostTracker:
    """Stores recent run costs keyed by session_id or trace_id."""

    _cache: ClassVar[dict[str, float]] = {}

    @classmethod
    def record_cost(cls, key: str | None, cost_usd: float) -> None:
        """Record the cost in USD for a specific session or trace key."""
        if key and cost_usd > 0:
            cls._cache[key] = cost_usd

    @classmethod
    def get_cost(cls, key: str | None) -> float | None:
        """Retrieve recorded cost in USD for a given key, if present."""
        if not key:
            return None
        return cls._cache.get(key)

    @classmethod
    def clear(cls) -> None:
        """Clear all cached run costs."""
        cls._cache.clear()
