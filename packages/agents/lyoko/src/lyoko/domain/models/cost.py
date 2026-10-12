"""In-memory run cost registry for tracking real-time LLM token execution costs."""

from collections import OrderedDict
from typing import ClassVar


class RunCostTracker:
    """Stores recent run costs keyed by session_id or trace_id with FIFO eviction."""

    _MAX_SIZE: ClassVar[int] = 1000
    _cache: ClassVar[OrderedDict[str, float]] = OrderedDict()

    @classmethod
    def record_cost(cls, key: str | None, cost_usd: float) -> None:
        """Record the cost in USD for a specific session or trace key."""
        if not key or cost_usd <= 0:
            return
        if key in cls._cache:
            cls._cache.move_to_end(key)
        cls._cache[key] = cost_usd
        if len(cls._cache) > cls._MAX_SIZE:
            cls._cache.popitem(last=False)

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
