"""Agent runner executing LLM chat turns under ToolGate."""

import logging
from typing import Any

from langchain_core.runnables import RunnableConfig
from lyoko.application.safety.tool_gate import ToolGate
from lyoko.config import settings
from lyoko.domain.interfaces.llm import LLMClientInterface
from lyoko.domain.interfaces.mcp import MCPClientInterface

logger = logging.getLogger("lyoko.agent_runner")


def status_callback(config: Any) -> Any:
    """Return the streaming status callback carried by the run config, if any."""
    if not isinstance(config, dict):
        return None
    return config.get("configurable", {}).get("on_status")


class AgentRunner:
    """Runs Hermes agent turns with every tool call going through ToolGate."""

    def __init__(
        self,
        mcp_client: MCPClientInterface | None = None,
        llm: LLMClientInterface | None = None,
        default_max_steps: int | None = None,
    ) -> None:
        self.mcp_client = mcp_client
        self.llm = llm
        self.default_max_steps = default_max_steps

    @staticmethod
    def extract_status_callback(config: Any) -> Any:
        """Extract on_status callback from runnable config."""
        return status_callback(config)

    async def run(
        self,
        state: dict[str, Any],
        gate: ToolGate,
        config: RunnableConfig | None = None,
        mcp_client: Any = None,
        llm: LLMClientInterface | None = None,
        *,
        phase: str,
        system_prompt: str,
        prompt: str,
        on_status: Any = None,
    ) -> str:
        """Run Hermes agent with every tool call going through ``gate``."""
        effective_llm = llm if llm is not None else self.llm
        if effective_llm is None:
            raise ValueError("llm client is required to run agent")

        effective_mcp = mcp_client if mcp_client is not None else self.mcp_client
        tools = (
            effective_mcp.get_langchain_tools(authorizer=gate.authorize) if effective_mcp else []
        )
        max_steps = self.default_max_steps or settings.max_agent_steps
        effective_on_status = (
            on_status if on_status is not None else self.extract_status_callback(config)
        )

        kwargs: dict[str, Any] = {
            "prompt": prompt,
            "system_prompt": system_prompt,
            "tools": tools,
            "session_id": state.get("session_id"),
            "trace_name": f"{phase}-agent",
            "tags": [f"phase:{phase}"],
            "max_steps": max_steps,
            "parent_config": config,
            "metadata": {"trace_id": state.get("trace_id")},
        }
        if effective_on_status is not None:
            kwargs["on_status"] = effective_on_status
        return await effective_llm.chat(**kwargs)

    async def __call__(
        self,
        state: dict[str, Any],
        gate: ToolGate,
        config: RunnableConfig | None = None,
        mcp_client: Any = None,
        llm: LLMClientInterface | None = None,
        *,
        phase: str,
        system_prompt: str,
        prompt: str,
        on_status: Any = None,
    ) -> str:
        """Allow calling the runner instance directly as an async callable."""
        return await self.run(
            state=state,
            gate=gate,
            config=config,
            mcp_client=mcp_client,
            llm=llm,
            phase=phase,
            system_prompt=system_prompt,
            prompt=prompt,
            on_status=on_status,
        )


_DEFAULT_RUNNER = AgentRunner()
run_agent = _DEFAULT_RUNNER.run
