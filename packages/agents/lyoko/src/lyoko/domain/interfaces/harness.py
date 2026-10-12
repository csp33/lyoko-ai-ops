"""Domain port interfaces for agent execution harnesses."""

from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import Any


class HarnessRunnerInterface(ABC):
    """Abstract port for running a bounded ReAct tool-use harness loop."""

    @abstractmethod
    async def run(
        self,
        prompt_text: str,
        *,
        system_prompt: str | None,
        model_with_tools: Any,
        summary_client: Any,
        tools_by_name: dict[str, Any],
        max_iterations: int,
        session_id: str | None = None,
        trace_id: str | None = None,
        on_status: Callable[[str], Any] | None = None,
    ) -> str:
        """Run the bounded harness loop until the model completes or a limit trips."""


class AgentHarnessInterface(ABC):
    """Abstract port for running high-level bounded reasoning and tool execution loops."""

    @abstractmethod
    async def execute(
        self,
        prompt: str,
        *,
        system_prompt: str | None = None,
        tools: list[Any] | None = None,
        session_id: str | None = None,
        trace_name: str | None = None,
        tags: list[str] | None = None,
        max_steps: int | None = None,
        parent_config: dict[str, Any] | None = None,
        on_status: Callable[[str], Any] | None = None,
    ) -> str:
        """Execute the harness loop until the agent completes or a budget trips."""
