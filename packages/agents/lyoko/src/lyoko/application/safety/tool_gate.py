"""Tool-call gate: decides whether an agent's tool call runs, needs approval, or is refused."""

import fnmatch
import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from lyoko.application.safety.hitl.descriptor import ApprovalActionDescriptor
from lyoko.domain.interfaces.approval import ApprovalManagerInterface
from lyoko.domain.interfaces.chat_service import ChatServiceInterface
from lyoko.domain.models.chat import ApprovalAction, ApprovalRequest

logger = logging.getLogger("lyoko.tool_gate")

# Above this size the approval arguments are omitted: the Action/Plan lines describe the change.
_MAX_ARGUMENTS_PREVIEW_CHARS = 300

ReadOnlyLookup = Callable[[str], bool | None]
"""Returns an upstream's explicit read-only hint for a tool, or ``None`` when it declared none."""


class GateMode(StrEnum):
    """What the gate does with a tool that is neither read-only nor auto-approved."""

    READ_ONLY = "read_only"
    """Refuse it. Used while investigating and verifying, when nothing may change."""

    APPROVAL = "approval"
    """Ask the operator before running it. Used while remediating."""


class CallOutcome(StrEnum):
    AUTO_APPROVED = "auto_approved"
    APPROVED = "approved"
    DENIED = "denied"
    REFUSED = "refused"


@dataclass(frozen=True)
class ToolCallRecord:
    """A state-changing tool call the gate had to decide on (read-only calls are not recorded)."""

    tool: str
    arguments: dict[str, Any]
    outcome: CallOutcome
    reason: str = ""

    @property
    def executed(self) -> bool:
        return self.outcome in (CallOutcome.AUTO_APPROVED, CallOutcome.APPROVED)

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool": self.tool,
            "arguments": self.arguments,
            "outcome": self.outcome.value,
            "reason": self.reason,
        }


def matches_any(tool_name: str, patterns: Sequence[str]) -> bool:
    """Return True when ``tool_name`` matches one of the glob ``patterns`` (case-sensitive)."""
    return any(fnmatch.fnmatchcase(tool_name, pattern) for pattern in patterns)


class ToolGate:
    """Authorizes the tool calls of one agent run.

    The gate serves any run, an incident phase or a chat reply. ``event_id`` identifies the run
    and prefixes its approval ids (they end up in Telegram callback data, limited to 64 bytes, so
    keep it short). ``origin`` is the line shown to the operator saying what triggered the run.
    Replies to an approval message continue ``session_id``, which defaults to ``event_id``.

    Policy, evaluated in order:

    1. If the upstream declared the tool read-only, it always runs and is not recorded.
    2. A tool matching ``read_only_patterns`` always runs and is not recorded.
    3. In ``APPROVAL`` mode, a tool matching ``auto_approved_patterns`` runs and is recorded.
    4. Anything else needs the operator: in ``APPROVAL`` mode they are asked through the chat
       connectors and the call waits for their answer; in ``READ_ONLY`` mode the call is refused.

    The upstream hint wins over the name patterns: an explicit "not read-only" is never rescued by
    a matching name, so a declared state-changing tool cannot slip through a heuristic. When the
    upstream declared nothing the name patterns decide, as before.

    Refusals are returned as text so the agent can adapt instead of crashing. When no approval
    channel is available the gate refuses rather than guessing, so a missing channel can never
    turn into an unattended change.
    """

    def __init__(
        self,
        *,
        mode: GateMode,
        read_only_patterns: Sequence[str],
        auto_approved_patterns: Sequence[str] = (),
        event_id: str,
        origin: str,
        session_id: str | None = None,
        chat_id: str = "",
        message_thread_id: str | int | None = None,
        plan: str = "",
        approval_manager: ApprovalManagerInterface | None = None,
        chat_manager: ChatServiceInterface | None = None,
        readonly_lookup: ReadOnlyLookup | None = None,
    ) -> None:
        self.mode = mode
        self.records: list[ToolCallRecord] = []
        self._read_only_patterns = tuple(read_only_patterns)
        self._auto_approved_patterns = tuple(auto_approved_patterns)
        self._event_id = event_id
        self._origin = origin
        self._session_id = session_id or event_id
        self._chat_id = chat_id
        self._message_thread_id = message_thread_id
        self._plan = plan
        self._approval_manager = approval_manager
        self._chat_manager = chat_manager
        self._readonly_lookup = readonly_lookup
        self._approval_count = 0

    async def authorize(self, tool_name: str, arguments: dict[str, Any]) -> str | None:
        """Return ``None`` to allow the call, or a refusal message for the agent."""
        declared = self._readonly_lookup(tool_name) if self._readonly_lookup is not None else None
        if declared is True:
            return None

        # An explicit "not read-only" from the upstream beats the name heuristic; only when the
        # upstream said nothing (None) do the patterns get to decide.
        if declared is None and matches_any(tool_name, self._read_only_patterns):
            return None

        if self.mode is GateMode.READ_ONLY:
            return self._refuse(
                tool_name,
                arguments,
                f"'{tool_name}' may change state and this phase is read-only. Do not call it. "
                "If it is needed, include it in your plan instead.",
            )

        if matches_any(tool_name, self._auto_approved_patterns):
            self._record(tool_name, arguments, CallOutcome.AUTO_APPROVED)
            return None

        return await self._ask_operator(tool_name, arguments)

    def _record(
        self, tool_name: str, arguments: dict[str, Any], outcome: CallOutcome, reason: str = ""
    ) -> None:
        self.records.append(ToolCallRecord(tool_name, arguments, outcome, reason))

    def _refuse(self, tool_name: str, arguments: dict[str, Any], message: str) -> str:
        self._record(tool_name, arguments, CallOutcome.REFUSED, message)
        logger.warning("Refused tool call '%s' for event %s", tool_name, self._event_id)
        return f"Refused: {message}"

    async def _ask_operator(self, tool_name: str, arguments: dict[str, Any]) -> str | None:
        if self._approval_manager is None or self._chat_manager is None:
            return self._refuse(
                tool_name,
                arguments,
                f"'{tool_name}' requires operator approval but no approval channel is configured.",
            )

        self._approval_count += 1
        approval_id = f"{self._event_id}.{self._approval_count}"
        action_summary = ApprovalActionDescriptor.describe_action(tool_name, arguments)
        details_parts = [f"Action: {action_summary}"]
        if self._plan:
            details_parts.append(f"Plan: {self._plan}")
        details_parts.extend([self._origin, f"Tool: `{tool_name}`"])
        # Only show the arguments when they are short enough to read; a full resource manifest is
        # noise, and the Action/Plan lines already say what will happen.
        args_text = ApprovalActionDescriptor.format_arguments(arguments)
        if "truncated" not in args_text and len(args_text) <= _MAX_ARGUMENTS_PREVIEW_CHARS:
            details_parts.append(f"Arguments:\n```json\n{args_text}\n```")
        request = ApprovalRequest(
            incident_id=approval_id,
            session_id=self._session_id,
            title=f"Approval required: {tool_name}",
            details="\n\n".join(details_parts),
            chat_id=self._chat_id,
            actions=[
                ApprovalAction(action_id="approve", label="✅ Approve", style="primary"),
                ApprovalAction(action_id="reject", label="❌ Deny", style="danger"),
            ],
        )
        await self._chat_manager.broadcast_approval_request(
            request,
            message_thread_id=self._message_thread_id,
            reply_to_message_id=self._message_thread_id,
        )
        response = await self._approval_manager.wait_for_approval(approval_id)

        if response.approved:
            self._record(tool_name, arguments, CallOutcome.APPROVED)
            return None

        reason = response.reason or "denied by the operator"
        self._record(tool_name, arguments, CallOutcome.DENIED, reason)
        logger.warning("Operator denied '%s' for event %s: %s", tool_name, self._event_id, reason)
        return (
            f"Denied: the operator did not approve '{tool_name}' ({reason}). Do not retry it or "
            "look for a way around the denial. Stop and report what you found."
        )


class ToolGateFactory:
    """Factory for configuring and creating ToolGate instances."""

    @staticmethod
    def create_gate(
        state: dict[str, Any],
        mode: GateMode,
        approval_manager: ApprovalManagerInterface | None = None,
        chat_manager: ChatServiceInterface | None = None,
        plan: str = "",
        mcp_client: Any = None,
    ) -> ToolGate:
        from lyoko.application.context.formatter import IncidentContextFormatter
        from lyoko.config import settings

        alert_name = state.get("alert_name") or "event"
        event_id = state.get("event_id") or f"incident-{alert_name}"
        readonly_lookup: ReadOnlyLookup | None = getattr(mcp_client, "is_read_only", None)
        return ToolGate(
            mode=mode,
            read_only_patterns=settings.read_only_tools,
            auto_approved_patterns=settings.auto_approved_tools,
            event_id=event_id,
            origin=IncidentContextFormatter.format_origin(state),
            session_id=state.get("session_id"),
            chat_id=state.get("chat_id") or settings.telegram_default_chat_id or "",
            message_thread_id=state.get("message_thread_id"),
            plan=plan,
            approval_manager=approval_manager,
            chat_manager=chat_manager,
            readonly_lookup=readonly_lookup,
        )


make_gate = ToolGateFactory.create_gate
