"""Unit tests for ToolStatusFormatter in application/chat layer."""

from lyoko.application.chat.status_formatter import ToolStatusFormatter


def test_formats_gateway_tool_call():
    formatter = ToolStatusFormatter()
    res = formatter.format_tool_status(
        {"name": "gateway_call_tool", "args": {"tool_name": "k8s_get_pods"}},
        turns_used=2,
        max_turns=8,
        cost_usd=0.015,
    )
    assert res == "🛰️ Calling <code>k8s_get_pods</code> (Step 3/8 • $0.015)"


def test_formats_discovery_and_schema():
    formatter = ToolStatusFormatter()
    res_domain = formatter.format_tool_status(
        {"name": "gateway_get_domain_tools", "args": {"domain": "unifi"}}
    )
    assert res_domain == "🔍 Discovering unifi tools"

    res_schema = formatter.format_tool_status(
        {"name": "gateway_get_tool_schema", "args": {"tool_name": "k8s_patch"}}
    )
    assert res_schema == "🔍 Reading the <code>k8s_patch</code> schema"


def test_formats_generic_tool_without_budget_info():
    formatter = ToolStatusFormatter()
    res = formatter.format_tool_status({"name": "custom_diagnostic_tool", "args": {}})
    assert res == "🛰️ Calling <code>custom_diagnostic_tool</code>"
