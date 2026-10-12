"""Domain guardrail definitions and safety policies for LYOKO."""

# Tools whose names indicate they only read state. Matching is conservative on purpose: a tool
# that does not match needs operator approval, so an unknown or ambiguous name fails safe.
DEFAULT_READ_ONLY_TOOLS: tuple[str, ...] = (
    "gateway_get_domain_tools",
    "gateway_get_tool_schema",
    "get_*",
    "list_*",
    "search_*",
    "query_*",
    "*_get",
    "*_get_*",
    "*_list",
    "*_list_*",
    "*_log",
    "*_logs",
    "*_top",
    "*_search",
    "*_search_*",
    "*_query",
    "*_query_*",
    "*_stats_summary",
    "*_tool_index",
)
