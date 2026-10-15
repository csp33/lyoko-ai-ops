"""Tests for the factual progress line shown while the ReAct agent runs tools."""

import pytest
from lyoko.infrastructure.llm.tool_loop import _tool_status


@pytest.mark.parametrize(
    ("tool_call", "expected"),
    [
        (
            {"name": "gateway_call_tool", "args": {"tool_name": "kubectl_get", "arguments": {}}},
            "🛰️ Calling <code>kubectl_get</code>",
        ),
        (
            {"name": "gateway_get_domain_tools", "args": {"domain": "unifi"}},
            "🔍 Discovering unifi tools",
        ),
        (
            {"name": "gateway_get_tool_schema", "args": {"tool_name": "kubectl_get"}},
            "🔍 Reading the <code>kubectl_get</code> schema",
        ),
        (
            {"name": "custom_tool", "args": {}},
            "🛰️ Calling <code>custom_tool</code>",
        ),
    ],
)
def test_tool_status_renders_factual_progress(tool_call, expected):
    """Verify each known gateway/delegation tool maps to a clear, factual status line."""
    assert _tool_status(tool_call) == expected
