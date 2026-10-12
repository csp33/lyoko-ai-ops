"""Test doubles for the incident agent: a scripted LLM and a gateway client that expose the gate."""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from lyoko.application.safety.hitl.manager import ApprovalManager
from lyoko.domain.interfaces.llm import LLMClientInterface
from lyoko.domain.interfaces.mcp import ToolAuthorizer
from lyoko.domain.models.chat import ApprovalResponse

DIAGNOSIS_ACTIONABLE = (
    "ROOT_CAUSE: The radarr pod was OOMKilled because its memory limit is too low.\n"
    "ACTIONABLE: yes\n"
    "PLAN: 1. Call resources_scale for deployment radarr."
)
DIAGNOSIS_NOT_ACTIONABLE = (
    "ROOT_CAUSE: The database credentials are invalid.\n"
    "ACTIONABLE: no\n"
    "PLAN: A human should rotate the credentials."
)


@dataclass
class GateHandle:
    """Stands in for a LangChain tool list: carries the authorizer the agent must call."""

    authorizer: ToolAuthorizer | None


class FakeMCPClient:
    """Gateway client double that hands the gate's authorizer to the scripted LLM."""

    def get_langchain_tools(self, authorizer: ToolAuthorizer | None = None) -> list[Any]:
        return [GateHandle(authorizer)]

    async def get_domain_langchain_tools(
        self,
        domain: str,
        authorizer: ToolAuthorizer | None = None,
    ) -> list[Any]:
        return [GateHandle(authorizer)]


Behavior = Callable[[ToolAuthorizer], Awaitable[str]]


class ScriptedLLM(LLMClientInterface):
    """LLM double with one scripted behavior per phase (route, chat, diagnose, remediate, verify).

    A behavior receives the real gate authorizer, so it can attempt tool calls exactly like a
    ReAct agent would and the test observes the gate's real decisions.
    """

    def __init__(self, **behaviors: Behavior | str) -> None:
        self._behaviors = behaviors
        self.prompts: dict[str, str] = {}
        self.max_steps: list[int | None] = []
        self.parent_configs: dict[str, dict[str, Any] | None] = {}
        self.calls: list[str] = []

    async def chat(
        self,
        prompt: str,
        system_prompt: str | None = None,
        tools: list[Any] | None = None,
        session_id: str | None = None,
        user_id: str | None = None,
        trace_name: str | None = None,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        max_steps: int | None = None,
        parent_config: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> str:
        phase = next(t.split(":", 1)[1] for t in (tags or []) if t.startswith("phase:"))
        self.calls.append(phase)
        self.prompts[phase] = prompt
        self.parent_configs[phase] = parent_config
        self.max_steps.append(max_steps)
        behavior = self._behaviors[phase]
        if isinstance(behavior, str):
            return behavior
        assert tools, "the workflow must give the agent tools"
        return await behavior(tools[0].authorizer)


class Operator:
    """Answers approval requests the way a human tapping Telegram buttons would."""

    def __init__(self, manager: ApprovalManager, *, approve: bool, reason: str = "") -> None:
        self.requests = []
        self.thread_ids: list[str | int | None] = []
        self._manager = manager
        self._approve = approve
        self._reason = reason

    async def broadcast_approval_request(
        self, request, message_thread_id=None, reply_to_message_id=None
    ) -> None:
        self.requests.append(request)
        self.thread_ids.append(message_thread_id)
        asyncio.get_running_loop().call_later(
            0.01,
            lambda: self._manager.resolve_approval(
                ApprovalResponse(
                    incident_id=request.incident_id,
                    approved=self._approve,
                    user_id="admin",
                    reason=self._reason,
                )
            ),
        )
