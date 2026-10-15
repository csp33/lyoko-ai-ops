"""Use case for validating guardrails and executing tools on upstream MCP servers."""

import logging
from typing import Any

import yaml

from sector5_mcp.application.guardrail import GuardrailEngine
from sector5_mcp.application.registry import ToolRegistry
from sector5_mcp.domain.exceptions.tool import ToolNotFoundError
from sector5_mcp.domain.models.upstream import ToolResult

logger = logging.getLogger("sector5_mcp.application.use_cases.execute_tool")


class ExecuteToolUseCase:
    """Validates guardrails and executes a tool on the resolved upstream MCP server."""

    def __init__(self, registry: ToolRegistry, guardrail: GuardrailEngine) -> None:
        self.registry = registry
        self.guardrail = guardrail

    async def execute(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        """Validate safety guardrails and route tool execution to the upstream MCP server."""
        route = self.registry.resolve_tool_routing(name)
        if route is None:
            # Re-attempt discovery in case tools were registered dynamically
            await self.registry.discover_tools()
            route = self.registry.resolve_tool_routing(name)

        if route is None:
            suggestions = []
            if hasattr(self.registry, "suggest_tool_names"):
                suggestions = self.registry.suggest_tool_names(name)
            msg = f"Tool '{name}' not found on any upstream MCP server or blocked by policy."
            if suggestions:
                msg += f" Did you mean: {', '.join(suggestions)}?"
            raise ToolNotFoundError(msg)

        if len(route) == 3:
            resolved_name, upstream_name, upstream_client = route
        else:
            resolved_name = name
            upstream_name, upstream_client = route

        arguments = self._normalize_arguments(resolved_name, arguments, original_name=name)

        # Enforce all active security guardrails (namespace, command exec whitelist/blacklist, tool rules)
        self.guardrail.validate_tool_call(resolved_name, arguments, upstream_name=upstream_name)

        logger.info(
            f"Routing tool '{resolved_name}' (requested as '{name}') to upstream '{upstream_name}'..."
        )
        return await upstream_client.call_tool(resolved_name, arguments)

    @staticmethod
    def _normalize_arguments(
        tool_name: str, args: dict[str, Any], original_name: str = ""
    ) -> dict[str, Any]:
        """Normalize commonly confused parameters for upstream tools."""
        normalized = dict(args)
        if tool_name == "grafana_query_prometheus":
            if "expr" not in normalized:
                for alt_key in ("query", "target", "targets", "promql", "expression"):
                    if alt_key in normalized:
                        val = normalized.pop(alt_key)
                        if isinstance(val, list) and val:
                            val = val[0]
                        normalized["expr"] = str(val)
                        break
            normalized.setdefault("datasourceUid", "prometheus")
            normalized.setdefault("endTime", "now")
            if "queryType" not in normalized:
                normalized["queryType"] = "range" if "startTime" in normalized else "instant"
            if "stepSeconds" not in normalized and "step" in normalized:
                raw_step = normalized.pop("step")
                try:
                    if isinstance(raw_step, str):
                        clean = raw_step.strip().lower()
                        if clean.endswith("s"):
                            normalized["stepSeconds"] = int(clean[:-1])
                        elif clean.endswith("m"):
                            normalized["stepSeconds"] = int(clean[:-1]) * 60
                        else:
                            normalized["stepSeconds"] = int(clean)
                    else:
                        normalized["stepSeconds"] = int(raw_step)
                except (ValueError, TypeError):
                    pass
            for time_key in ("startTime", "endTime"):
                val = normalized.get(time_key)
                if isinstance(val, str) and any(
                    y in val for y in ("2020-", "2021-", "2022-", "2023-", "2024-", "2025-")
                ):
                    normalized[time_key] = "now-30m" if time_key == "startTime" else "now"
            valid_grafana_keys = {
                "datasourceUid",
                "endTime",
                "expr",
                "projectName",
                "queryType",
                "startTime",
                "stepSeconds",
            }
            normalized = {k: v for k, v in normalized.items() if k in valid_grafana_keys}
        elif tool_name == "grafana_api_request":
            if "endpoint" not in normalized:
                if "url" in normalized:
                    normalized["endpoint"] = normalized.pop("url")
                elif "path" in normalized:
                    normalized["endpoint"] = normalized.pop("path")
            normalized.setdefault("method", "GET")
        elif tool_name in (
            "k8s_resources_get",
            "k8s_resources_list",
            "k8s_resources_delete",
            "k8s_resources_scale",
        ):
            if "api_version" in normalized and "apiVersion" not in normalized:
                normalized["apiVersion"] = normalized.pop("api_version")
            elif "apiversion" in normalized and "apiVersion" not in normalized:
                normalized["apiVersion"] = normalized.pop("apiversion")

            if "name" not in normalized:
                for alt_name in ("app", "application", "app_name", "appName", "resource_name"):
                    if alt_name in normalized:
                        normalized["name"] = normalized.pop(alt_name)
                        break

            is_app_intent = (
                "application" in original_name.lower()
                or "argo" in original_name.lower()
                or normalized.get("namespace") == "argocd"
            )
            if "kind" not in normalized and is_app_intent:
                normalized["kind"] = "Application"
                normalized.setdefault("apiVersion", "argoproj.io/v1alpha1")
                normalized.setdefault("namespace", "argocd")
        elif tool_name in ("k8s_pods_get", "k8s_pods_log"):
            if "name" not in normalized:
                for alt_name in ("pod", "pod_name", "podName"):
                    if alt_name in normalized:
                        normalized["name"] = normalized.pop(alt_name)
                        break
        elif tool_name == "k8s_resources_create_or_update":
            body = (
                normalized.pop("body", None)
                or normalized.pop("manifest", None)
                or normalized.pop("patch", None)
            )
            if body is not None:
                if isinstance(body, dict):
                    group = normalized.pop("group", None)
                    version = normalized.pop("version", None)
                    if "apiVersion" not in body and group and version:
                        body["apiVersion"] = f"{group}/{version}"
                    elif "apiVersion" not in body and (
                        "argo" in original_name.lower() or normalized.get("namespace") == "argocd"
                    ):
                        body["apiVersion"] = "argoproj.io/v1alpha1"

                    if "kind" not in body:
                        plural = normalized.pop("plural", "")
                        if (
                            plural.lower() in ("applications", "application")
                            or "argo" in original_name.lower()
                        ):
                            body["kind"] = "Application"
                        elif plural:
                            body["kind"] = plural.rstrip("s").capitalize()

                    meta = body.setdefault("metadata", {})
                    if "name" in normalized and "name" not in meta:
                        meta["name"] = normalized["name"]
                    if "namespace" in normalized and "namespace" not in meta:
                        meta["namespace"] = normalized["namespace"]
                    normalized["resource"] = yaml.dump(body, sort_keys=False)
                else:
                    normalized["resource"] = str(body)
            elif "resource" in normalized and isinstance(normalized["resource"], dict):
                normalized["resource"] = yaml.dump(normalized["resource"], sort_keys=False)
        elif tool_name == "k8s_kubectl":
            if "command" not in normalized:
                for alt_name in ("cmd", "args", "arguments", "c"):
                    if alt_name in normalized:
                        normalized["command"] = normalized.pop(alt_name)
                        break
        return normalized
