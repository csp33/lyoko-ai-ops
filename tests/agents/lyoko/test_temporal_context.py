"""Unit tests for runtime temporal context injection."""

from datetime import UTC, datetime

from lyoko.application.context.temporal import build_temporal_context


def test_build_temporal_context_with_fixed_time():
    fixed = datetime(2026, 10, 12, 10, 30, 0, tzinfo=UTC)
    result = build_temporal_context(now=fixed)

    assert "--- RUNTIME TEMPORAL CONTEXT ---" in result
    assert "Current time: 2026-10-12 10:30:00Z (Monday)" in result
    assert "authoritative baseline" in result


def test_build_temporal_context_default_uses_current_utc():
    result = build_temporal_context()
    assert "--- RUNTIME TEMPORAL CONTEXT ---" in result
    assert "UTC" in result or "Z" in result
