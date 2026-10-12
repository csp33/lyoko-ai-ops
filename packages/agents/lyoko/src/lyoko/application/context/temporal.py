"""Runtime temporal context utilities for injecting authoritative timestamps into agents."""

from datetime import UTC, datetime


def build_temporal_context(now: datetime | None = None) -> str:
    """Format an authoritative runtime temporal context block for prompt injection.

    Ensures agents anchor relative time calculations (e.g. 'today', 'this week', 'last 24h')
    to the current UTC date, time, and day of the week.
    """
    current = now or datetime.now(UTC)
    iso_time = current.strftime("%Y-%m-%d %H:%M:%SZ")
    day_name = current.strftime("%A")
    return (
        "--- RUNTIME TEMPORAL CONTEXT ---\n"
        f"Current time: {iso_time} ({day_name})\n"
        "Use this timestamp as the authoritative baseline for relative time calculations (e.g. today, this week).\n"
        "--------------------------------"
    )
