"""Guardrail service enforcing security whitelists and execution boundaries."""

import fnmatch
import logging
import re
import shlex
from typing import Any

import yaml

from sector5_mcp.domain.exceptions.guardrail import GuardrailViolationError
from sector5_mcp.domain.models.guardrail import GuardrailPolicy

logger = logging.getLogger("sector5_mcp.guardrails")


class GuardrailEngine:
    """Evaluates security rules, tool whitelists, and command execution guardrails."""

    def __init__(self, policy: GuardrailPolicy | None = None):
        self.policy = policy or GuardrailPolicy()

    def is_tool_allowed(self, tool_name: str) -> bool:
        """Check if a tool is permitted by the allowlist and not blocked by the denylist."""
        # 1. Denylist takes absolute precedence
        for pattern in self.policy.blocked_tools:
            if fnmatch.fnmatch(tool_name, pattern):
                return False

        # 2. Allowlist check
        return any(fnmatch.fnmatch(tool_name, pattern) for pattern in self.policy.allowed_tools)

    def validate_tool_call(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        upstream_name: str = "",
    ) -> None:
        """Validate tool invocation against all active guardrail rules.

        Raises:
            GuardrailViolationError: If the call violates any security rule.
        """
        # 1. Tool allowlist check
        if not self.is_tool_allowed(tool_name):
            raise GuardrailViolationError(
                f"Security Guardrail: Tool '{tool_name}' is not permitted by the tool whitelist."
            )

        # 2. Kubernetes protected namespaces check
        if upstream_name == "kubernetes":
            self._validate_kubernetes_namespaces(tool_name, arguments)

        # 3. GitHub repository allowlist / denylist check
        if upstream_name == "github" or tool_name.startswith(("github_", "gh_")):
            self._validate_github_repo(arguments)

        # 4. Deep inspection of container exec / command execution arguments
        self._inspect_exec_commands(tool_name, arguments)

    def _is_read_only_tool(self, tool_name: str) -> bool:
        """Return True when the tool name matches a read-only pattern."""
        return any(fnmatch.fnmatchcase(tool_name, p) for p in self.policy.read_only_tools)

    def _validate_kubernetes_namespaces(self, tool_name: str, arguments: dict[str, Any]) -> None:
        """Block every non read-only Kubernetes tool that targets a protected namespace.

        The target namespace is read from the `namespace`/`ns` arguments, from the manifest of
        tools that take a `resource` document, and from the name of a Namespace object (which is
        itself the namespace being changed). Tools that do not match a read-only pattern are
        treated as mutations, so a new or unknown tool cannot bypass the check.
        """
        if not self.policy.blocked_namespaces or self._is_read_only_tool(tool_name):
            return

        namespaces, inspectable = self._target_namespaces(arguments)
        if not inspectable:
            raise GuardrailViolationError(
                f"Security Guardrail: The manifest passed to '{tool_name}' cannot be inspected, "
                "so it cannot be proven to stay out of the protected namespaces."
            )

        for namespace in sorted(namespaces):
            if namespace in self.policy.blocked_namespaces:
                raise GuardrailViolationError(
                    f"Security Guardrail: Mutation or exec operations in protected namespace '{namespace}' are prohibited."
                )

    @staticmethod
    def _target_namespaces(arguments: dict[str, Any]) -> tuple[set[str], bool]:
        """Collect the namespaces a Kubernetes call targets.

        Returns the namespaces and whether the arguments could be fully inspected.
        """
        found: set[str] = set()

        for key in ("namespace", "ns"):
            value = arguments.get(key)
            if isinstance(value, str) and value:
                found.add(value)

        # Tools addressing an object by apiVersion, kind and name (e.g. resources_delete).
        if str(arguments.get("kind", "")).lower() == "namespace" and arguments.get("name"):
            found.add(str(arguments["name"]))

        manifest = arguments.get("resource")
        if manifest is None:
            return found, True
        if not isinstance(manifest, str):
            documents: list[Any] = [manifest]
        else:
            try:
                documents = list(yaml.safe_load_all(manifest))
            except yaml.YAMLError:
                return found, False

        pending = list(documents)
        while pending:
            doc = pending.pop()
            if doc is None:
                continue
            if not isinstance(doc, dict):
                return found, False
            metadata = doc.get("metadata") if isinstance(doc.get("metadata"), dict) else {}
            if metadata.get("namespace"):
                found.add(str(metadata["namespace"]))
            if str(doc.get("kind", "")).lower() == "namespace" and metadata.get("name"):
                found.add(str(metadata["name"]))
            if isinstance(doc.get("items"), list):
                pending.extend(doc["items"])

        return found, True

    def _validate_github_repo(self, arguments: dict[str, Any]) -> None:
        """Validate that target GitHub repository is permitted by policy."""
        repo = arguments.get("repo") or arguments.get("repository")
        if not repo and "owner" in arguments and "repo" in arguments:
            repo = f"{arguments['owner']}/{arguments['repo']}"

        if not repo or not isinstance(repo, str):
            return

        # 1. Blocked repos check
        for pattern in self.policy.blocked_github_repos:
            if fnmatch.fnmatch(repo, pattern):
                raise GuardrailViolationError(
                    f"Security Guardrail: GitHub repository '{repo}' is blocked by security policy."
                )

        # 2. Allowed repos check
        if not any(fnmatch.fnmatch(repo, pattern) for pattern in self.policy.allowed_github_repos):
            raise GuardrailViolationError(
                f"Security Guardrail: GitHub repository '{repo}' is not permitted by allowed repositories policy."
            )

    def _inspect_exec_commands(self, tool_name: str, arguments: dict[str, Any]) -> None:
        """Inspect command strings or lists passed to execution tools to prevent destructive commands."""
        # Extract command strings from common parameter names
        command_candidates: list[str] = []

        for key in ["command", "cmd", "args", "script", "command_line", "exec"]:
            if key in arguments:
                val = arguments[key]
                if isinstance(val, str):
                    command_candidates.append(val)
                elif isinstance(val, list):
                    # List of arguments e.g. ["rm", "-rf", "/tmp"]
                    command_candidates.append(" ".join(str(item) for item in val))
                elif isinstance(val, dict):
                    # Nested structures
                    command_candidates.extend(
                        str(v) for v in val.values() if isinstance(v, (str, list))
                    )

        if not command_candidates and not any(
            k in tool_name.lower() for k in ["exec", "shell", "bash", "run_cmd"]
        ):
            return

        for cmd_str in command_candidates:
            # Check blocked regex patterns (e.g. \brm\b, \bdd\b, etc.)
            for pattern in self.policy.blocked_exec_patterns:
                if re.search(pattern, cmd_str, re.IGNORECASE):
                    raise GuardrailViolationError(
                        f"Security Guardrail: Command '{cmd_str}' violates blocked execution pattern '{pattern}'."
                    )

            # Check allowed exec binaries whitelist if configured
            if self.policy.allowed_exec_commands:
                try:
                    tokens = shlex.split(cmd_str)
                except Exception:
                    tokens = cmd_str.split()

                if tokens:
                    base_binary = tokens[0].split("/")[-1]
                    # In case of subshell wrappers e.g. "sh -c '...'", extract the actual command inside
                    if (
                        base_binary in ["sh", "bash", "zsh"]
                        and len(tokens) >= 3
                        and tokens[1] == "-c"
                    ):
                        inner_cmd = tokens[2]
                        try:
                            inner_tokens = shlex.split(inner_cmd)
                            if inner_tokens:
                                base_binary = inner_tokens[0].split("/")[-1]
                        except Exception:
                            pass

                    if base_binary not in self.policy.allowed_exec_commands:
                        raise GuardrailViolationError(
                            f"Security Guardrail: Command binary '{base_binary}' is not in the allowed commands whitelist."
                        )
