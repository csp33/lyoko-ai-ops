"""Detection of failure and rejection markers in tool execution outputs."""

_DEFAULT_ERROR_MARKERS: tuple[str, ...] = (
    "'is_error': true",
    '"is_error": true',
    '"is_error":true',
    "error executing tool",
    "not found on any upstream",
    "is forbidden:",
    "permission denied",
)


class ToolErrorDetector:
    """Detects whether a tool output indicates an execution failure or authorization error."""

    def __init__(self, error_markers: tuple[str, ...] | None = None) -> None:
        self._markers = error_markers or _DEFAULT_ERROR_MARKERS

    def is_tool_error(self, output: str) -> bool:
        """Return True if tool output string contains any failure markers."""
        lowered = output.lower()
        return any(marker in lowered for marker in self._markers)
