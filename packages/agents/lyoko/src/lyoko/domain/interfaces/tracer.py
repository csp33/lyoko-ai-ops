"""Tracer interface definition for LYOKO."""

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from typing import Any


class TracerInterface(ABC):
    @abstractmethod
    def get_callback_handler(self) -> Any | None:
        """Return callback handler for LangGraph / LangChain tracing."""

    async def traced(self, name: str, run: Callable[[], Awaitable[Any]]) -> Any:
        """Run an infrastructure call as a named step in the current trace.

        Code paths that call infrastructure directly (no LLM/LangChain run to trace) wrap each
        step in this, so it shows up under the enclosing graph node instead of an untraced gap.
        The default runs the call as-is; tracers that support it may emit a child span.
        """
        return await run()

    def get_trace_url(self, trace_id: str) -> str | None:
        """Return the URL to view the trace in the observability UI, if available."""
        return None

    def flush(self) -> None:
        """Flush any pending trace observations to the observability backend."""
        return None

    def get_trace_config(
        self,
        *,
        trace_id: str | None = None,
        session_id: str | None = None,
        user_id: str | None = None,
        trace_name: str | None = None,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Build a LangChain / LangGraph RunnableConfig dict with tracing, session, and metadata."""
        config: dict[str, Any] = {}
        handler = self.get_callback_handler()
        if handler:
            config["callbacks"] = [handler]
        if tags:
            config["tags"] = list(tags)
        if metadata:
            config["metadata"] = dict(metadata)
        if trace_name:
            config["run_name"] = trace_name
        return config
