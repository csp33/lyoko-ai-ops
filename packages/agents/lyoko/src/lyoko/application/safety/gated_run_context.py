"""Binds the tool calls made by an external agent runtime to the run that is currently gated.

An always-on Hermes gateway reaches its tools through LYOKO's gated MCP proxy. The proxy has no
notion of which operator request it is serving, so the bridge activates the callback of the
running turn (budget guard + ``ToolGate`` + output buffering) for the duration of the call, and
the proxy forwards every tool call to it.

Policy:

- Fail closed: with no active run, every call is refused.
- One gated run at a time: the lock serialises runs so an approval prompt and its tool call can
  never be attributed to another operator request.
"""

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

logger = logging.getLogger("lyoko.application.safety.gated_run_context")

GatedToolCall = Callable[[str, dict[str, Any]], Awaitable[str]]

_NO_ACTIVE_RUN = (
    "Refused: no operator request is active, so tool calls are not allowed. "
    "Tools can only be used while LYOKO is serving a request."
)


class GatedRunContext:
    """Holds the gated tool callback of the single run being served."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._call: GatedToolCall | None = None

    @property
    def is_active(self) -> bool:
        return self._call is not None

    @asynccontextmanager
    async def activate(self, on_tool_call: GatedToolCall) -> AsyncIterator[None]:
        """Make ``on_tool_call`` the only route for proxied tool calls until the run ends."""
        async with self._lock:
            self._call = on_tool_call
            try:
                yield
            finally:
                self._call = None

    async def call(self, tool_name: str, arguments: dict[str, Any]) -> str:
        """Run a proxied tool call through the active run's gate, or refuse it."""
        handler = self._call
        if handler is None:
            logger.warning("Refused proxied call to '%s': no active run.", tool_name)
            return _NO_ACTIVE_RUN
        return await handler(tool_name, arguments)


_context: GatedRunContext | None = None


def get_gated_run_context() -> GatedRunContext:
    """Return the process-wide context shared by the Hermes bridge and the MCP proxy."""
    global _context
    if _context is None:
        _context = GatedRunContext()
    return _context
