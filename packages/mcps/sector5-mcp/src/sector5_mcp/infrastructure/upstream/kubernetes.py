"""Kubernetes Upstream MCP Adapter with manifest sanitization and pruning."""

import logging
from typing import Any

import yaml

from sector5_mcp.domain.models.upstream import ToolResult
from sector5_mcp.infrastructure.upstream.client import ProcessUpstreamClient

logger = logging.getLogger("sector5_mcp.upstream_kubernetes")

PRUNABLE_TOOLS = {
    "k8s_resources_get",
    "k8s_pods_get",
    "k8s_resources_list",
    "resources_get",
    "pods_get",
    "resources_list",
}

_NOISY_METADATA_FIELDS = (
    "managedFields",
    "generation",
    "resourceVersion",
    "uid",
)
_LAST_APPLIED_CONFIG_ANNOTATION = "kubectl.kubernetes.io/last-applied-configuration"


def prune_k8s_manifest(data: Any) -> Any:
    """Recursively prune verbose, non-informative metadata from Kubernetes manifests."""
    if isinstance(data, list):
        return [prune_k8s_manifest(item) for item in data]

    if not isinstance(data, dict):
        return data

    # If it is a list resource (e.g. kind: List, PodList)
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


class KubernetesUpstreamClient(ProcessUpstreamClient):
    """Adapter for Kubernetes MCP server with manifest sanitization and pruning."""

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        """Execute Kubernetes tool and sanitize manifest content on inspection tools."""
        result = await super().call_tool(name, arguments)
        if result.is_error or not result.content:
            return result

        raw_name = name
        if self.prefix and raw_name.startswith(self.prefix):
            raw_name = raw_name[len(self.prefix) :]

        if name in PRUNABLE_TOOLS or raw_name in PRUNABLE_TOOLS:
            sanitized = sanitize_manifest_content(result.content)
            return ToolResult(
                status=result.status,
                content=sanitized,
                is_error=result.is_error,
            )

        return result
