"""Worker script running under .venv-hermes to execute NousResearch hermes-agent.

Communicates with the host LYOKO process via newline-delimited JSON over stdin/stdout.
"""

import json
import logging
import sys
import traceback

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("lyoko.hermes_worker")


def main() -> None:
    # Read initial config from stdin
    line = sys.stdin.readline()
    if not line:
        logger.error("No configuration received on stdin.")
        sys.exit(1)

    try:
        config = json.loads(line)
    except Exception as exc:
        logger.error("Failed to parse config JSON: %s", exc)
        sys.exit(1)

    prompt = config.get("prompt", "")
    system_prompt = config.get("system_prompt")
    model = config.get("model", "gpt-4o-mini")
    api_key = config.get("api_key", "")
    base_url = config.get("base_url") or "https://api.openai.com/v1"
    max_iterations = int(config.get("max_iterations", 5))
    tool_defs = config.get("tools", [])

    # Register tools into hermes tool registry
    try:
        from tools.registry import registry
    except ImportError as exc:
        logger.error("Failed to import hermes tools.registry: %s", exc)
        sys.exit(1)

    registered_names = []
    for tool_def in tool_defs:
        tool_name = tool_def["name"]
        registered_names.append(tool_name)

        def make_handler(name: str):
            def _handler(args: dict, **kwargs):
                # Request tool execution from LYOKO host
                req = {
                    "type": "call_tool",
                    "tool_name": name,
                    "arguments": args or {},
                }
                sys.stdout.write(json.dumps(req) + "\n")
                sys.stdout.flush()

                resp_line = sys.stdin.readline()
                if not resp_line:
                    return json.dumps({"error": "Host closed pipe before returning tool result"})
                try:
                    resp = json.loads(resp_line)
                    if resp.get("status") == "error":
                        return json.dumps({"error": resp.get("error", "Tool execution failed")})
                    return str(resp.get("output", ""))
                except Exception as ex:
                    return json.dumps({"error": f"Failed to parse tool result: {ex}"})

            return _handler

        registry.register(
            name=tool_name,
            toolset="lyoko",
            schema=tool_def,
            handler=make_handler(tool_name),
            override=True,
        )

    def on_tool_start(tool_name: str, tool_args: dict) -> None:
        msg = {
            "type": "status",
            "text": f"🛰️ Calling {tool_name}",
        }
        sys.stdout.write(json.dumps(msg) + "\n")
        sys.stdout.flush()

    try:
        from hermes_cli.plugins import get_plugin_manager

        def on_post_api_request(**kwargs):
            usage = kwargs.get("usage") or {}
            usage_msg = {
                "type": "llm_usage",
                "model": kwargs.get("model") or model,
                "input_tokens": usage.get("prompt_tokens") or usage.get("input_tokens") or 0,
                "output_tokens": usage.get("completion_tokens") or usage.get("output_tokens") or 0,
                "total_tokens": usage.get("total_tokens") or 0,
                "duration": kwargs.get("api_duration") or 0.0,
            }
            sys.stdout.write(json.dumps(usage_msg) + "\n")
            sys.stdout.flush()

        pm = get_plugin_manager()
        pm._hooks.setdefault("post_api_request", []).append(on_post_api_request)
    except Exception as hook_err:
        logger.debug("Could not register post_api_request hook: %s", hook_err)

    try:
        import run_agent

        agent = run_agent.AIAgent(
            provider="openai",
            model=model,
            api_key=api_key,
            base_url=base_url,
            enabled_toolsets=["lyoko"] if registered_names else None,
            max_iterations=max_iterations,
            tool_start_callback=on_tool_start,
            quiet_mode=True,
            skip_memory=True,
            skip_context_files=True,
        )

        result = agent.run_conversation(
            user_message=prompt,
            system_message=system_prompt,
        )

        final_response = result.get("final_response", "")
        out = {
            "type": "result",
            "output": final_response,
        }
        sys.stdout.write(json.dumps(out) + "\n")
        sys.stdout.flush()

    except Exception as exc:
        err_msg = {
            "type": "error",
            "error": f"{type(exc).__name__}: {exc}",
            "traceback": traceback.format_exc(),
        }
        sys.stdout.write(json.dumps(err_msg) + "\n")
        sys.stdout.flush()
        sys.exit(1)


if __name__ == "__main__":
    main()
