"""Service managing smart head-and-tail output truncation and scratchpad offloading."""

from typing import Any

from lyoko.domain.interfaces.scratchpad import ScratchpadStorageInterface
from lyoko.domain.models.scratchpad import SmartBufferConfig


class SmartOutputBufferService:
    """Buffers tool outputs, preserving head and tail context while offloading large payloads."""

    def __init__(
        self,
        storage: ScratchpadStorageInterface | None = None,
        config: SmartBufferConfig | None = None,
    ) -> None:
        self._storage = storage
        self._config = config or SmartBufferConfig()

    @property
    def config(self) -> SmartBufferConfig:
        """The active buffer configuration."""
        return self._config

    def _truncate_head_tail(self, text: str) -> tuple[str, str, int]:
        max_chars = self._config.max_chars
        head_chars = max(100, int(max_chars * self._config.head_ratio))
        tail_chars = max(100, max_chars - head_chars - 120)

        head = text[:head_chars]
        tail = text[-tail_chars:]
        omitted = max(0, len(text) - (len(head) + len(tail)))
        return head, tail, omitted

    async def process_output(
        self,
        tool_name: str,
        output: Any,
        session_id: str | None = None,
    ) -> str:
        """Process and bound tool output, offloading oversized payloads to storage if needed."""
        text = str(output)
        if len(text) <= self._config.max_chars:
            return text

        head, tail, omitted = self._truncate_head_tail(text)

        if len(text) >= self._config.offload_threshold_chars and self._storage is not None:
            ref = await self._storage.store_output(
                tool_name=tool_name,
                raw_output=text,
                session_id=session_id,
            )
            note = (
                f"[System Note: Tool output ({len(text)} chars) exceeded limit. "
                f"Full output saved to scratchpad file: {ref.file_path} "
                f"({ref.line_count} lines, {ref.size_bytes} bytes). "
                f"Head & tail preview shown below.]"
            )
        else:
            note = (
                f"[System Note: Tool output truncated. {omitted} characters omitted "
                "to fit the context budget. Head & tail preview shown below.]"
            )

        return f"{head}\n\n{note}\n\n{tail}"
