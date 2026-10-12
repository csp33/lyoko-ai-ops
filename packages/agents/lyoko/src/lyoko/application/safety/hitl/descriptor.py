"""Human-readable rendering of the action an approval request asks the operator to authorize."""

import json
from typing import Any

import yaml

_MAX_ARGUMENTS_CHARS = 1500
_MAX_ARG_VALUE_CHARS = 280


class ApprovalActionDescriptor:
    """Renders human-readable summaries and formatted previews for approval requests."""

    @staticmethod
    def _resource_identity(resource: Any) -> tuple[str, str] | None:
        """Return ``(kind, name)`` from a resource manifest string, or ``None`` if not parseable."""
        if not isinstance(resource, str):
            return None
        try:
            document = yaml.safe_load(resource)
        except yaml.YAMLError:
            return None
        if not isinstance(document, dict) or not document.get("kind"):
            return None
        name = (document.get("metadata") or {}).get("name")
        return str(document["kind"]), str(name) if name else ""

    @classmethod
    def describe_action(cls, tool_name: str, arguments: dict[str, Any]) -> str:
        """Produce a concise human-readable sentence explaining what the tool call wants to do."""
        if "scale" in tool_name:
            name = (
                arguments.get("name")
                or arguments.get("deployment")
                or arguments.get("workload")
                or "workload"
            )
            replicas = arguments.get("replicas")
            ns = f" in namespace '{arguments['namespace']}'" if arguments.get("namespace") else ""
            return f"Scale {name} to {replicas} replicas{ns}."

        if "delete" in tool_name:
            target = (
                arguments.get("name")
                or arguments.get("pod")
                or arguments.get("deployment")
                or arguments.get("target")
                or "resource"
            )
            ns = f" from namespace '{arguments['namespace']}'" if arguments.get("namespace") else ""
            return f"Delete {target}{ns}."

        if "restart" in tool_name:
            target = arguments.get("name") or arguments.get("deployment") or "workload"
            ns = f" in namespace '{arguments['namespace']}'" if arguments.get("namespace") else ""
            return f"Restart {target}{ns}."

        if (
            "create" in tool_name
            or "update" in tool_name
            or "apply" in tool_name
            or "patch" in tool_name
        ):
            identity = cls._resource_identity(arguments.get("resource"))
            if identity is not None:
                kind, name = identity
                return f"Apply changes to {kind} '{name}'." if name else f"Apply changes to {kind}."
            kind = arguments.get("kind") or "resource"
            name = arguments.get("name") or ""
            ns = f" in namespace '{arguments['namespace']}'" if arguments.get("namespace") else ""
            target = f"{kind} '{name}'" if name else kind
            return f"Apply changes to {target}{ns}."

        if tool_name.startswith("ha_") or "homeassistant" in tool_name:
            service = arguments.get("service") or arguments.get("domain") or tool_name
            entity = arguments.get("entity_id") or ""
            return f"Execute Home Assistant action '{service}'{' on ' + entity if entity else ''}."

        if tool_name.startswith("unifi_"):
            action = arguments.get("action") or arguments.get("command") or tool_name
            return f"Execute UniFi network action '{action}'."

        readable_name = tool_name.replace("_", " ").strip()
        key_params = [
            f"{k}='{v}'"
            for k, v in arguments.items()
            if k in ("name", "namespace", "replicas", "entity_id", "service", "action", "command")
        ]
        if key_params:
            return f"Execute {readable_name} with {', '.join(key_params)}."
        return f"Execute tool '{tool_name}'."

    @staticmethod
    def format_arguments(arguments: dict[str, Any]) -> str:
        """Render tool arguments for the approval prompt, previewing oversized values."""

        def _preview(value: Any) -> Any:
            if isinstance(value, str) and len(value) > _MAX_ARG_VALUE_CHARS:
                return " ".join(value[:_MAX_ARG_VALUE_CHARS].split()) + " … (truncated)"
            return value

        redacted = {key: _preview(value) for key, value in arguments.items()}
        text = json.dumps(redacted, indent=2, default=str, ensure_ascii=False)
        if len(text) > _MAX_ARGUMENTS_CHARS:
            return text[:_MAX_ARGUMENTS_CHARS] + "\n... (truncated)"
        return text
