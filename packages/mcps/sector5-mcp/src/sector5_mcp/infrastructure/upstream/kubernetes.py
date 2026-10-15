"""Kubernetes Upstream Adapter with native kubectl execution, sanitization, and pruning."""

import asyncio
import json
import logging
import os
import shlex
from typing import Any

import yaml

from sector5_mcp.domain.interfaces.upstream import UpstreamMCPInterface
from sector5_mcp.domain.models.upstream import ToolDefinition, ToolResult, UpstreamType

logger = logging.getLogger("sector5_mcp.upstream_kubernetes")

_NOISY_METADATA_FIELDS = (
    "managedFields",
    "generation",
    "resourceVersion",
    "uid",
)
_LAST_APPLIED_CONFIG_ANNOTATION = "kubectl.kubernetes.io/last-applied-configuration"
_SHELL_OPERATORS = ("|", "||", ";", "&&", ">", ">>", "<")


def _has_shell_operators(cmd: str | list[str], args: list[str]) -> bool:
    """Detect shell piping, redirection, or chaining in kubectl arguments."""
    return any(
        arg in _SHELL_OPERATORS or arg.startswith(("|", "||", ";", "&&")) for arg in args
    ) or (
        isinstance(cmd, str)
        and any(op in cmd for op in (" |", "| ", " ;", "; ", " &&", "&& ", " >", " >"))
    )


def prune_k8s_manifest(data: Any) -> Any:
    """Recursively prune verbose, non-informative metadata from Kubernetes manifests."""
    if isinstance(data, list):
        return [prune_k8s_manifest(item) for item in data]

    if not isinstance(data, dict):
        return data

    if "items" in data and isinstance(data["items"], list):
        data["items"] = [prune_k8s_manifest(item) for item in data["items"]]

    metadata = data.get("metadata")
    if isinstance(metadata, dict):
        for field in _NOISY_METADATA_FIELDS:
            metadata.pop(field, None)

        annotations = metadata.get("annotations")
        if isinstance(annotations, dict):
            annotations.pop(_LAST_APPLIED_CONFIG_ANNOTATION, None)

    return data


def sanitize_manifest_content(content: str) -> str:
    """Parse YAML or JSON content, prune noisy metadata, and return clean YAML."""
    try:
        docs = list(yaml.safe_load_all(content))
        if not docs or not any(isinstance(d, (dict, list)) for d in docs):
            return content

        pruned_docs = [
            prune_k8s_manifest(doc) if isinstance(doc, (dict, list)) else doc for doc in docs
        ]

        if len(pruned_docs) == 1:
            return yaml.dump(pruned_docs[0], sort_keys=False, default_flow_style=False).strip()
        return yaml.dump_all(pruned_docs, sort_keys=False, default_flow_style=False).strip()
    except Exception as exc:
        logger.debug("Failed to sanitize Kubernetes manifest content: %s", exc)
        return content


class KubernetesUpstreamClient(UpstreamMCPInterface):
    """Native Kubernetes adapter executing kubectl commands with sanitization and pruning."""

    def __init__(
        self,
        command: str = "kubectl",
        env: dict[str, str] | None = None,
        upstream_type: UpstreamType = UpstreamType.KUBERNETES,
        prefix: str = "k8s_",
    ) -> None:
        self.command = command
        self.env = env or {}
        self.upstream_type = upstream_type
        self.prefix = prefix

    async def list_tools(self) -> list[ToolDefinition]:
        """Expose k8s_kubectl as the sole tool of the kubernetes domain."""
        return [
            ToolDefinition(
                name="k8s_kubectl",
                description=(
                    "Execute a kubectl command against the Kubernetes cluster. "
                    "Executes kubectl directly as a binary without a shell: pipes ('|'), grep/awk, "
                    "redirection, and chaining are NOT supported. "
                    "Supports inspection ('get', 'describe', 'logs', 'top') and mutations "
                    "('apply', 'patch', 'scale', 'rollout', 'delete'). "
                    "Examples: 'get pods -A', 'get pods -n argocd', 'describe app bentopdf -n argocd', "
                    "'logs deploy/bentopdf -n bentopdf --tail=50', 'get events -n default'."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "command": {
                            "type": "string",
                            "description": (
                                "The kubectl subcommand and arguments, e.g. 'get pods -A' or "
                                "'get pods -n argocd'. Do not use shell pipes ('|') or redirects."
                            ),
                        }
                    },
                    "required": ["command"],
                },
                upstream_type=self.upstream_type,
            ),
        ]

    async def _exec_kubectl(self, args: list[str], stdin_data: str | None = None) -> ToolResult:
        run_env = os.environ.copy()
        run_env.update(self.env)
        try:
            proc = await asyncio.create_subprocess_exec(
                self.command,
                *args,
                stdin=asyncio.subprocess.PIPE if stdin_data else None,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=run_env,
            )
            stdout_b, stderr_b = await proc.communicate(
                input=stdin_data.encode("utf-8") if stdin_data else None
            )
        except FileNotFoundError:
            return ToolResult(
                status="failed",
                content=f"Error: '{self.command}' binary not found on the system.",
                is_error=True,
            )
        except Exception as exc:
            return ToolResult(
                status="failed", content=f"Failed to execute {self.command}: {exc}", is_error=True
            )

        stdout = stdout_b.decode("utf-8", errors="replace").strip()
        stderr = stderr_b.decode("utf-8", errors="replace").strip()

        if proc.returncode != 0:
            err_msg = stderr or stdout or f"Command exited with code {proc.returncode}"
            return ToolResult(status="failed", content=err_msg, is_error=True)

        content = sanitize_manifest_content(stdout)
        return ToolResult(status="success", content=content, is_error=False)

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        raw = name[len(self.prefix) :] if self.prefix and name.startswith(self.prefix) else name

        if raw in ("kubectl", "run_kubectl"):
            cmd = arguments.get("command") or arguments.get("cmd") or ""
            if isinstance(cmd, list):
                args = [str(c) for c in cmd]
            elif isinstance(cmd, str):
                try:
                    args = shlex.split(cmd)
                except Exception:
                    args = cmd.split()
            else:
                args = []
            if args and args[0].lower() == "kubectl":
                args = args[1:]
            if not args:
                return ToolResult(
                    status="failed", content="Missing or empty kubectl command.", is_error=True
                )
            if _has_shell_operators(cmd, args):
                return ToolResult(
                    status="failed",
                    content=(
                        "Shell pipelines ('|'), chaining (';', '&&'), and redirection ('>', '<') "
                        "are not supported because 'k8s_kubectl' executes the kubectl binary directly without a shell. "
                        "Use native kubectl options such as '-o jsonpath=...', '-o custom-columns=...', "
                        "-l <label-selector>, or query specific namespaces with '-n <namespace>'."
                    ),
                    is_error=True,
                )
            return await self._exec_kubectl(args)

        if raw in ("pods_get", "pod_get"):
            ns = arguments.get("namespace", "default")
            return await self._exec_kubectl(
                ["get", "pod", arguments.get("name", ""), "-n", ns, "-o", "yaml"]
            )

        if raw in ("pods_list", "pod_list"):
            ns = arguments.get("namespace")
            args = ["get", "pods", "-o", "yaml"]
            args.extend(["-n", ns] if ns else ["-A"])
            return await self._exec_kubectl(args)

        if raw in ("pods_log", "pod_log", "pods_logs"):
            ns = arguments.get("namespace", "default")
            args = ["logs", arguments.get("name", ""), "-n", ns]
            if arguments.get("container"):
                args.extend(["-c", str(arguments["container"])])
            if arguments.get("tail"):
                args.extend(["--tail", str(arguments["tail"])])
            return await self._exec_kubectl(args)

        if raw in ("resources_get", "resource_get"):
            kind = arguments.get("kind", "")
            name_val = arguments.get("name", "")
            ns = arguments.get("namespace", "default")
            target = f"{kind}/{name_val}" if name_val else kind
            return await self._exec_kubectl(["get", target, "-n", ns, "-o", "yaml"])

        if raw in ("resources_list", "resource_list"):
            kind = arguments.get("kind", "")
            args = ["get", kind, "-o", "yaml"]
            args.extend(["-n", arguments["namespace"]] if arguments.get("namespace") else ["-A"])
            if arguments.get("labelSelector"):
                args.extend(["-l", str(arguments["labelSelector"])])
            return await self._exec_kubectl(args)

        if raw == "resources_create_or_update":
            manifest = arguments.get("resource", "")
            if isinstance(manifest, dict):
                manifest = yaml.dump(manifest, sort_keys=False)
            return await self._exec_kubectl(["apply", "-f", "-"], stdin_data=str(manifest))

        if raw in ("resources_patch", "resource_patch", "patch_namespaced_custom_object"):
            kind = arguments.get("kind") or "Application"
            name_val = arguments.get("name", "")
            ns = arguments.get("namespace", "default")
            patch_data = arguments.get("patch") or arguments.get("body") or ""
            patch_str = json.dumps(patch_data) if isinstance(patch_data, dict) else str(patch_data)
            patch_type = arguments.get("patch_type", "merge")
            return await self._exec_kubectl(
                ["patch", f"{kind}/{name_val}", "-n", ns, f"--type={patch_type}", "-p", patch_str]
            )

        if raw in ("resources_delete", "resource_delete"):
            kind = arguments.get("kind", "")
            name_val = arguments.get("name", "")
            ns = arguments.get("namespace", "default")
            target = f"{kind}/{name_val}" if name_val else kind
            return await self._exec_kubectl(["delete", target, "-n", ns])

        if raw in ("resources_scale", "resource_scale"):
            kind = arguments.get("kind", "deployment")
            name_val = arguments.get("name", "")
            ns = arguments.get("namespace", "default")
            scale = str(arguments.get("scale", 1))
            return await self._exec_kubectl(
                ["scale", f"{kind}/{name_val}", f"--replicas={scale}", "-n", ns]
            )

        if raw == "events_list":
            args = ["get", "events", "-o", "yaml"]
            args.extend(["-n", arguments["namespace"]] if arguments.get("namespace") else ["-A"])
            return await self._exec_kubectl(args)

        if raw == "namespaces_list":
            return await self._exec_kubectl(["get", "namespaces", "-o", "yaml"])

        return ToolResult(
            status="failed", content=f"Unknown Kubernetes tool '{name}'.", is_error=True
        )
