"""Unit and integration tests for HermesSubprocessBridge and IPC communication."""

from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from lyoko.infrastructure.llm.hermes_bridge import HermesSubprocessBridge


@pytest.mark.asyncio
async def test_bridge_is_available(tmp_path: Path):
    # Test availability detection when venv python and worker script exist
    fake_python = tmp_path / "bin" / "python"
    fake_python.parent.mkdir(parents=True, exist_ok=True)
    fake_python.touch()
    fake_worker = tmp_path / "worker.py"
    fake_worker.touch()

    bridge = HermesSubprocessBridge(python_bin=fake_python, worker_script=fake_worker)
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
import sys, json, os

# 1. Read config
line = sys.stdin.readline()
cfg = json.loads(line)
assert cfg.get("session_id") == "test-session-123"
assert os.environ.get("HERMES_SESSION_ID") == "test-session-123"
assert cfg.get("trace_id") == "test-trace-abc"
assert os.environ.get("HERMES_TRACE_ID") == "test-trace-abc"

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
        session_id="test-session-123",
        trace_id="test-trace-abc",
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


@pytest.mark.asyncio
async def test_bridge_ipc_protocol_llm_usage(tmp_path: Path):
    """Test that llm_usage IPC messages trigger the on_llm_usage callback."""
    worker_script = tmp_path / "usage_worker.py"
    worker_script.write_text(
        """
import sys, json

sys.stdin.readline()
sys.stdout.write(json.dumps({
    "type": "llm_usage",
    "model": "gpt-4o-mini",
    "input_tokens": 120,
    "output_tokens": 45,
    "total_tokens": 165
}) + "\\n")
sys.stdout.flush()

sys.stdout.write(json.dumps({"type": "result", "output": "Done with usage"}) + "\\n")
sys.stdout.flush()
"""
    )

    received_usage = []

    async def on_usage(msg: dict) -> None:
        received_usage.append(msg)

    bridge = HermesSubprocessBridge(
        python_bin=Path.cwd() / ".venv" / "bin" / "python",
        worker_script=worker_script,
        hermes_home=tmp_path / ".hermes_test",
    )

    res = await bridge.execute(
        prompt="Test usage",
        model="gpt-4o-mini",
        api_key="sk-test",
        on_tool_call=AsyncMock(),
        on_llm_usage=on_usage,
    )

    assert res == "Done with usage"
    assert len(received_usage) == 1
    assert received_usage[0]["input_tokens"] == 120
    assert received_usage[0]["output_tokens"] == 45
    assert (tmp_path / ".hermes_test").exists()


@pytest.mark.asyncio
async def test_bridge_times_out_on_unresponsive_worker(tmp_path: Path):
    worker_script = tmp_path / "hanging_worker.py"
    worker_script.write_text(
        """
import sys, time
sys.stdin.readline()
time.sleep(10)
"""
    )
    bridge = HermesSubprocessBridge(
        python_bin=Path.cwd() / ".venv" / "bin" / "python",
        worker_script=worker_script,
        hermes_home=tmp_path / ".hermes_test",
    )
    with pytest.raises(TimeoutError, match="Hermes worker timed out"):
        await bridge.execute(
            prompt="Test timeout",
            model="gpt-4o-mini",
            api_key="sk-test",
            on_tool_call=AsyncMock(),
            timeout_seconds=0.5,
        )


@pytest.mark.asyncio
async def test_bridge_remote_server_execution(monkeypatch: pytest.MonkeyPatch):
    """Test executing against a remote Hermes gateway via HTTP/SSE."""
    bridge = HermesSubprocessBridge(server_url="http://fake-hermes:8642")
    assert bridge.is_available() is True

    import httpx

    sse_lines = [
        "event: hermes.tool.progress\n",
        'data: {"tool": "terminal", "label": "kubectl get pods", "status": "running"}\n',
        "\n",
        'data: {"id": "chatcmpl-1", "choices": [{"delta": {"content": "Pods: "}}]}\n',
        "\n",
        'data: {"id": "chatcmpl-1", "choices": [{"delta": {"content": "pod-1 running"}}], "usage": {"prompt_tokens": 50, "completion_tokens": 20, "total_tokens": 70}}\n',
        "\n",
        "data: [DONE]\n",
    ]

    class MockStreamResponse:
        status_code = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

        async def aiter_lines(self):
            for line in sse_lines:
                yield line

    class MockAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

        def stream(self, method: str, url: str, **kwargs):
            assert method == "POST"
            assert url == "http://fake-hermes:8642/v1/chat/completions"
            body = kwargs.get("json", {})
            assert body["messages"][-1]["content"] == "Check remote pods"
            return MockStreamResponse()

    monkeypatch.setattr(httpx, "AsyncClient", MockAsyncClient)

    statuses = []
    usages = []

    async def mock_on_tool_call(tool_name: str, args: dict) -> str:
        return "pod-1 running"

    async def mock_on_status(text: str) -> None:
        statuses.append(text)

    async def mock_on_usage(usage: dict) -> None:
        usages.append(usage)

    result = await bridge.execute(
        prompt="Check remote pods",
        model="gpt-4o-mini",
        api_key="sk-test",
        session_id="session-remote-1",
        on_tool_call=mock_on_tool_call,
        on_status=mock_on_status,
        on_llm_usage=mock_on_usage,
    )

    assert result == "Pods: pod-1 running"
    assert statuses == ["🛠️ kubectl get pods"]
    assert len(usages) == 1
    assert usages[0]["total_tokens"] == 70


@pytest.mark.asyncio
async def test_bridge_filters_lyoko_mcp_progress_events(monkeypatch: pytest.MonkeyPatch):
    """Test that mcp__lyoko__ and gateway_ events are not duplicated to on_status."""
    bridge = HermesSubprocessBridge(server_url="http://fake-hermes:8642")
    import httpx

    sse_lines = [
        "event: hermes.tool.progress\n",
        'data: {"tool": "mcp__lyoko__gateway_call_tool", "label": "mcp__lyoko__gateway_call_tool", "status": "running"}\n',
        "\n",
        "event: hermes.tool.progress\n",
        'data: {"tool": "gateway_get_domain_tools", "label": "gateway_get_domain_tools", "status": "running"}\n',
        "\n",
        "event: hermes.tool.progress\n",
        'data: {"label": "Reading tool details · 2 tools", "status": "running"}\n',
        "\n",
        'data: {"id": "chatcmpl-2", "choices": [{"delta": {"content": "Answer"}}]}\n',
        "\n",
        "data: [DONE]\n",
    ]

    class MockStreamResponse:
        status_code = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

        async def aiter_lines(self):
            for line in sse_lines:
                yield line

    class MockAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

        def stream(self, method: str, url: str, **kwargs):
            return MockStreamResponse()

    monkeypatch.setattr(httpx, "AsyncClient", MockAsyncClient)

    statuses = []

    async def mock_on_status(text: str) -> None:
        statuses.append(text)

    result = await bridge.execute(
        prompt="Test filtering",
        model="gpt-4o-mini",
        api_key="sk-test",
        on_tool_call=AsyncMock(),
        on_status=mock_on_status,
    )

    assert result == "Answer"
    # mcp__lyoko__ and gateway_ were filtered out; only general progress was kept
    assert statuses == ["🛠️ Reading tool details · 2 tools"]
