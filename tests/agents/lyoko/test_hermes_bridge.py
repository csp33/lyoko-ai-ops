"""Unit and integration tests for HermesSubprocessBridge and IPC communication."""

from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from lyoko.infrastructure.llm.hermes_bridge import HermesSubprocessBridge


@pytest.mark.asyncio
async def test_bridge_is_available():
    bridge = HermesSubprocessBridge()
    # In this workspace, .venv-hermes exists
    assert bridge.is_available() is True


@pytest.mark.asyncio
async def test_bridge_not_available_raises():
    bridge = HermesSubprocessBridge(python_bin="/non/existent/path/python")
    assert bridge.is_available() is False
    with pytest.raises(FileNotFoundError, match="Hermes virtual environment not found"):
        await bridge.execute(
            prompt="Hello",
            model="gpt-4o-mini",
            api_key="sk-test",
            on_tool_call=AsyncMock(),
        )


@pytest.mark.asyncio
async def test_bridge_ipc_protocol_mock_worker(tmp_path: Path):
    """Test the bidirectional IPC message protocol using a simulated worker script."""
    worker_script = tmp_path / "mock_worker.py"
    worker_script.write_text(
        """
import sys, json

# 1. Read config
line = sys.stdin.readline()
cfg = json.loads(line)

# 2. Emit status
sys.stdout.write(json.dumps({"type": "status", "text": "Starting analysis"}) + "\\n")
sys.stdout.flush()

# 3. Call tool
sys.stdout.write(json.dumps({"type": "call_tool", "tool_name": "k8s_get_pods", "arguments": {"ns": "default"}}) + "\\n")
sys.stdout.flush()

# 4. Read tool result
tool_res = json.loads(sys.stdin.readline())

# 5. Emit final result
final_output = f"Pods result: {tool_res.get('output')}"
sys.stdout.write(json.dumps({"type": "result", "output": final_output}) + "\\n")
sys.stdout.flush()
"""
    )

    statuses: list[str] = []

    async def on_status(text: str) -> None:
        statuses.append(text)

    async def on_tool_call(tool_name: str, args: dict) -> str:
        assert tool_name == "k8s_get_pods"
        assert args == {"ns": "default"}
        return "pod-1, pod-2"

    bridge = HermesSubprocessBridge(
        python_bin=Path.cwd() / ".venv" / "bin" / "python",
        worker_script=worker_script,
    )

    result = await bridge.execute(
        prompt="Check pods",
        model="gpt-4o-mini",
        api_key="sk-test",
        on_tool_call=on_tool_call,
        on_status=on_status,
    )

    assert result == "Pods result: pod-1, pod-2"
    assert statuses == ["Starting analysis"]


@pytest.mark.asyncio
async def test_bridge_handles_worker_error(tmp_path: Path):
    """Test that runtime errors in worker script are caught and raised."""
    worker_script = tmp_path / "error_worker.py"
    worker_script.write_text(
        """
import sys, json

# Read config
sys.stdin.readline()
# Emit error
sys.stdout.write(json.dumps({"type": "error", "error": "Fatal model crash"}) + "\\n")
sys.stdout.flush()
"""
    )

    bridge = HermesSubprocessBridge(
        python_bin=Path.cwd() / ".venv" / "bin" / "python",
        worker_script=worker_script,
    )

    with pytest.raises(RuntimeError, match="Hermes Agent error: Fatal model crash"):
        await bridge.execute(
            prompt="Hello",
            model="gpt-4o-mini",
            api_key="sk-test",
            on_tool_call=AsyncMock(),
        )


@pytest.mark.asyncio
async def test_bridge_toolgate_denial_handling(tmp_path: Path):
    """Test that a denied tool call sends error back to worker and worker reports denial."""
    worker_script = tmp_path / "denial_worker.py"
    worker_script.write_text(
        """
import sys, json

# 1. Read config
sys.stdin.readline()

# 2. Call mutating tool
sys.stdout.write(json.dumps({"type": "call_tool", "tool_name": "k8s_delete_pod", "arguments": {"pod": "bad-pod"}}) + "\\n")
sys.stdout.flush()

# 3. Read denial response
resp = json.loads(sys.stdin.readline())
assert resp["status"] == "error"
denial_reason = resp["error"]

# 4. Emit final response acknowledging denial
sys.stdout.write(json.dumps({"type": "result", "output": f"Operation aborted: {denial_reason}"}) + "\\n")
sys.stdout.flush()
"""
    )

    async def on_denied_tool(tool_name: str, args: dict) -> str:
        raise PermissionError("Denied: the operator did not approve 'k8s_delete_pod'")

    bridge = HermesSubprocessBridge(
        python_bin=Path.cwd() / ".venv" / "bin" / "python",
        worker_script=worker_script,
    )

    result = await bridge.execute(
        prompt="Delete pod",
        model="gpt-4o-mini",
        api_key="sk-test",
        on_tool_call=on_denied_tool,
    )

    assert "Operation aborted: Denied: the operator did not approve 'k8s_delete_pod'" in result
