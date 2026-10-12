"""Formatting of live tool execution status updates for chat channels."""

from typing import Any


class ToolStatusFormatter:
    """Formats live execution status messages for operator chat updates."""

    def format_tool_status(
        self,
        tool_call: dict[str, Any],
        turns_used: int | None = None,
        max_turns: int | None = None,
        cost_usd: float | None = None,
    ) -> str:
        """Format live status indicator for Telegram message drafts."""
        name = str(tool_call.get("name") or "tool")
        args = tool_call.get("args") or {}

        step_info = ""
        if turns_used is not None and max_turns is not None:
            if cost_usd is not None and cost_usd > 0:
                step_info = f" (Step {turns_used + 1}/{max_turns} • ${cost_usd:.3f})"
            else:
                step_info = f" (Step {turns_used + 1}/{max_turns})"

        if name.startswith("ask_") and name.endswith("_specialist"):
            return f"🧩 Consulting the {name[4:-11]} specialist{step_info}"
        if name == "gateway_call_tool":
            return f"🛰️ Calling <code>{args.get('tool_name', 'tool')}</code>{step_info}"
        if name == "gateway_get_domain_tools":
            return f"🔍 Discovering {args.get('domain', 'domain')} tools{step_info}"
        if name == "gateway_get_tool_schema":
            return f"🔍 Reading the <code>{args.get('tool_name', 'tool')}</code> schema{step_info}"
        return f"🛰️ Calling <code>{name}</code>{step_info}"
