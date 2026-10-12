"""Tests for SmartOutputBufferService."""

from pathlib import Path

import pytest
from lyoko.application.context.buffer import SmartOutputBufferService
from lyoko.domain.models.scratchpad import SmartBufferConfig
from lyoko.infrastructure.storage.scratchpad_file_storage import ScratchpadFileStorage


@pytest.mark.asyncio
async def test_small_output_not_truncated():
    buffer = SmartOutputBufferService()
    result = await buffer.process_output("k8s_get_pod", "Pod status is Running")
    assert result == "Pod status is Running"


@pytest.mark.asyncio
async def test_medium_output_head_tail_truncated_without_offload():
    config = SmartBufferConfig(max_chars=200, offload_threshold_chars=1000, head_ratio=0.25)
    buffer = SmartOutputBufferService(config=config)
    long_output = "HEAD_START_" + ("x" * 500) + "_TAIL_END"

    result = await buffer.process_output("k8s_logs", long_output)
    assert result.startswith("HEAD_START_")
    assert result.endswith("_TAIL_END")
    assert "System Note: Tool output truncated" in result
    assert "characters omitted to fit the context budget" in result


@pytest.mark.asyncio
async def test_large_output_offloaded_to_scratchpad_storage(tmp_path: Path):
    storage = ScratchpadFileStorage(base_dir=tmp_path)
    config = SmartBufferConfig(max_chars=200, offload_threshold_chars=500, head_ratio=0.25)
    buffer = SmartOutputBufferService(storage=storage, config=config)

    massive_output = "START_CONTEXT_" + ("ERROR_STACK_TRACE_" * 100) + "_FINAL_LINE"
    result = await buffer.process_output("k8s_logs", massive_output, session_id="test-session")

    assert "saved to scratchpad file:" in result
    assert str(tmp_path) in result
    assert result.startswith("START_CONTEXT_")
    assert result.endswith("_FINAL_LINE")

    # Verify file was actually created and holds the entire raw payload
    created_files = list(tmp_path.glob("scratch_*_k8s_logs.log"))
    assert len(created_files) == 1
    assert created_files[0].read_text(encoding="utf-8") == massive_output
