"""Subprocess bridge to execute the NousResearch hermes-agent in its isolated virtual environment."""

import asyncio
import contextlib
import json
import logging
import os
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from lyoko.application.safety.gated_run_context import get_gated_run_context
from lyoko.config import settings

logger = logging.getLogger("lyoko.infrastructure.llm.hermes_bridge")


def _resolve_hermes_python(configured_path: str) -> Path:
    """Resolve the python executable inside the hermes virtual environment."""
    p = Path(configured_path)
    if not p.is_absolute():
        cwd_candidate = Path.cwd() / p
        if cwd_candidate.exists():
            p = cwd_candidate
    if p.is_dir():
        return p / "bin" / "python"
    return p


class HermesSubprocessBridge:
    """Manages the lifecycle and IPC with the Hermes Agent worker process."""

    def __init__(
        self,
        python_bin: str | Path | None = None,
        worker_script: str | Path | None = None,
        hermes_home: str | Path | None = None,
        server_url: str | None = None,
    ) -> None:
        raw_python = str(python_bin or settings.hermes_venv_path)
        self.python_bin = _resolve_hermes_python(raw_python)
        if worker_script:
            self.worker_script = Path(worker_script)
        else:
            self.worker_script = Path(__file__).parent / "hermes_worker.py"
        raw_home = hermes_home or getattr(settings, "hermes_home", ".hermes")
        self.hermes_home = Path(raw_home).resolve()
        self.server_url = server_url or getattr(settings, "hermes_server_url", None)

    def is_available(self) -> bool:
        """Check if either a remote always-on daemon is configured or local worker exists."""
        if self.server_url:
            return True
        return self.python_bin.exists() and self.worker_script.exists()

    def _extract_tool_definitions(self, tools_by_name: dict[str, Any]) -> list[dict[str, Any]]:
        """Extract OpenAI-compatible function tool definitions from LangChain tools."""
        definitions: list[dict[str, Any]] = []
        for name, tool in tools_by_name.items():
            args_schema = getattr(tool, "args", {}) or {}
            definitions.append(
                {
                    "name": name,
                    "description": getattr(tool, "description", "") or "",
                    "parameters": {
                        "type": "object",
                        "properties": args_schema,
                    },
                }
            )
        return definitions

    async def execute(
        self,
        *,
        prompt: str,
        system_prompt: str | None = None,
        model: str,
        api_key: str,
        base_url: str | None = None,
        max_iterations: int = 5,
        tools_by_name: dict[str, Any] | None = None,
        session_id: str | None = None,
        trace_id: str | None = None,
        on_tool_call: Callable[[str, dict[str, Any]], Awaitable[str]],
        on_status: Callable[[str], Awaitable[None]] | None = None,
        on_llm_usage: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
        timeout_seconds: float = 180.0,
    ) -> str:
        """Spawn the worker process, handle streaming tool dispatches, and return final output."""
        if not self.is_available():
            raise FileNotFoundError(
                f"Hermes virtual environment not found at {self.python_bin}. "
                "Ensure .venv-hermes is created and hermes-agent is installed."
            )

        tools_by_name = tools_by_name or {}
        tool_defs = self._extract_tool_definitions(tools_by_name)

        config_payload = {
            "prompt": prompt,
            "system_prompt": system_prompt,
            "model": model,
            "api_key": api_key,
            "base_url": base_url,
            "max_iterations": max_iterations,
            "tools": tool_defs,
            "session_id": session_id,
            "trace_id": trace_id,
            "memory_enabled": getattr(settings, "hermes_memory_enabled", True),
        }

        self.hermes_home.mkdir(parents=True, exist_ok=True)
        env = {
            **os.environ,
            "HERMES_HOME": str(self.hermes_home),
        }
        if session_id:
            env["HERMES_SESSION_ID"] = str(session_id)
        if trace_id:
            env["HERMES_TRACE_ID"] = str(trace_id)
            env["LANGFUSE_TRACE_ID"] = str(trace_id)
        if settings.langfuse_enabled and settings.langfuse_public_key:
            env["HERMES_LANGFUSE_PUBLIC_KEY"] = settings.langfuse_public_key
            env["LANGFUSE_PUBLIC_KEY"] = settings.langfuse_public_key
            if settings.langfuse_secret_key:
                sec_val = (
                    settings.langfuse_secret_key.get_secret_value()
                    if hasattr(settings.langfuse_secret_key, "get_secret_value")
                    else str(settings.langfuse_secret_key)
                )
                env["HERMES_LANGFUSE_SECRET_KEY"] = sec_val
                env["LANGFUSE_SECRET_KEY"] = sec_val
            if settings.langfuse_host:
                env["HERMES_LANGFUSE_BASE_URL"] = settings.langfuse_host
                env["LANGFUSE_BASE_URL"] = settings.langfuse_host
            env["HERMES_LANGFUSE_ENV"] = settings.get_langfuse_environment()

        if self.server_url:
            return await self._execute_remote(
                config_payload=config_payload,
                on_tool_call=on_tool_call,
                on_status=on_status,
                on_llm_usage=on_llm_usage,
                timeout_seconds=timeout_seconds,
            )

        proc = await asyncio.create_subprocess_exec(
            str(self.python_bin),
            str(self.worker_script),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )

        assert proc.stdin is not None
        assert proc.stdout is not None
        assert proc.stderr is not None

        try:
            proc.stdin.write((json.dumps(config_payload) + "\n").encode("utf-8"))
            await proc.stdin.drain()

            while True:
                try:
                    line_bytes = await asyncio.wait_for(
                        proc.stdout.readline(), timeout=timeout_seconds
                    )
                except TimeoutError as err:
                    logger.error(
                        "Hermes worker timed out waiting for IPC response after %.1fs",
                        timeout_seconds,
                    )
                    raise TimeoutError(
                        f"Hermes worker timed out waiting for IPC response after {timeout_seconds}s"
                    ) from err
                if not line_bytes:
                    break
                line_str = line_bytes.decode("utf-8", errors="replace").strip()
                if not line_str:
                    continue

                try:
                    msg = json.loads(line_str)
                except Exception:
                    logger.debug("Non-JSON line from Hermes worker: %s", line_str)
                    continue

                msg_type = msg.get("type")
                if msg_type == "status":
                    text = msg.get("text", "")
                    if on_status is not None and text:
                        await on_status(text)

                elif msg_type == "llm_usage":
                    if on_llm_usage is not None:
                        await on_llm_usage(msg)

                elif msg_type == "call_tool":
                    tool_name = msg.get("tool_name", "")
                    tool_args = msg.get("arguments", {})
                    try:
                        output = await on_tool_call(tool_name, tool_args)
                        resp = {"status": "ok", "output": output}
                    except Exception as exc:
                        logger.warning("Tool execution error for '%s': %s", tool_name, exc)
                        resp = {"status": "error", "error": str(exc)}

                    proc.stdin.write((json.dumps(resp) + "\n").encode("utf-8"))
                    await proc.stdin.drain()

                elif msg_type == "result":
                    await proc.wait()
                    return str(msg.get("output", ""))

                elif msg_type == "error":
                    err_msg = msg.get("error", "Unknown error in Hermes worker")
                    logger.error("Hermes worker reported error: %s", err_msg)
                    await proc.wait()
                    raise RuntimeError(f"Hermes Agent error: {err_msg}")

            await proc.wait()
            stderr_bytes = await proc.stderr.read()
            stderr_text = stderr_bytes.decode("utf-8", errors="replace")
            if proc.returncode != 0:
                raise RuntimeError(
                    f"Hermes worker exited with code {proc.returncode}: {stderr_text}"
                )
            return ""

        finally:
            if proc.returncode is None:
                try:
                    proc.terminate()
                    await asyncio.wait_for(proc.wait(), timeout=2.0)
                except Exception:
                    with contextlib.suppress(Exception):
                        proc.kill()

    async def _execute_remote(
        self,
        *,
        config_payload: dict[str, Any],
        on_tool_call: Callable[[str, dict[str, Any]], Awaitable[str]],
        on_status: Callable[[str], Awaitable[None]] | None = None,
        on_llm_usage: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
        timeout_seconds: float = 180.0,
    ) -> str:
        """Connect to always-on Hermes gateway over HTTP/SSE, streaming deltas and tool progress."""
        import httpx

        base_url = str(self.server_url).rstrip("/")
        chat_url = f"{base_url}/v1/chat/completions"

        api_key = (
            settings.hermes_api_key.get_secret_value()
            if hasattr(settings.hermes_api_key, "get_secret_value")
            else str(settings.hermes_api_key or "lyoko-hermes-secret-key")
        )
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        session_id = config_payload.get("session_id")
        if session_id:
            headers["X-Hermes-Session-Id"] = str(session_id)
            headers["X-Hermes-Session-Key"] = f"lyoko-{session_id}"

        messages: list[dict[str, str]] = []
        system_prompt = config_payload.get("system_prompt")
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": config_payload.get("prompt", "")})

        body: dict[str, Any] = {
            "messages": messages,
            "stream": True,
        }
        requested_model = config_payload.get("model")
        if requested_model:
            body["model"] = str(requested_model)
            body["provider"] = "openai-api"

        full_content: list[str] = []
        timeout = httpx.Timeout(timeout_seconds, connect=10.0)

        async with (
            get_gated_run_context().activate(on_tool_call),
            httpx.AsyncClient(timeout=timeout) as client,
            client.stream("POST", chat_url, json=body, headers=headers) as resp,
        ):
            if resp.status_code != 200:
                err_body = await resp.aread()
                err_text = err_body.decode("utf-8", errors="replace")
                logger.error("Hermes gateway returned HTTP %s: %s", resp.status_code, err_text)
                raise RuntimeError(f"Hermes gateway HTTP {resp.status_code}: {err_text}")

            current_event: str | None = None
            async for line in resp.aiter_lines():
                line = line.strip()
                if not line:
                    current_event = None
                    continue

                if line.startswith("event:"):
                    current_event = line[len("event:") :].strip()
                    continue

                if line.startswith("data:"):
                    data_str = line[len("data:") :].strip()
                    if data_str == "[DONE]":
                        break

                    try:
                        data_json = json.loads(data_str)
                    except Exception:
                        continue

                    if current_event == "hermes.tool.progress":
                        tool_name = str(data_json.get("tool") or "")
                        label = str(data_json.get("label") or tool_name)
                        status = data_json.get("status")
                        is_lyoko_tool = tool_name.startswith(
                            ("mcp__lyoko__", "gateway_")
                        ) or label.startswith(("mcp__lyoko__", "gateway_"))
                        if (
                            on_status is not None
                            and label
                            and status == "running"
                            and not is_lyoko_tool
                        ):
                            await on_status(f"🛠️ {label}")
                        continue

                    choices = data_json.get("choices") or []
                    if choices:
                        delta = choices[0].get("delta") or {}
                        content_chunk = delta.get("content")
                        if content_chunk:
                            full_content.append(content_chunk)

                    usage = data_json.get("usage")
                    if usage and on_llm_usage is not None:
                        await on_llm_usage(
                            {
                                "model": data_json.get("model", "hermes-agent"),
                                "input_tokens": usage.get("prompt_tokens", 0),
                                "output_tokens": usage.get("completion_tokens", 0),
                                "total_tokens": usage.get("total_tokens", 0),
                            }
                        )

        return "".join(full_content).strip()
