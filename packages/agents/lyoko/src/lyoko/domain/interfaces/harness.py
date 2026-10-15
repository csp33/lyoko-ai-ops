"""Domain port interfaces for agent execution harnesses."""

from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import Any


class HarnessRunnerInterface(ABC):
    """Abstract port for running a bounded tool-use harness loop."""

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
