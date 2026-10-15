"""Unit tests for KubernetesUpstreamClient manifest sanitization and metadata pruning."""

from unittest.mock import AsyncMock, patch

import pytest
import yaml
from sector5_mcp.domain.models.upstream import ToolResult, UpstreamType
from sector5_mcp.infrastructure.upstream.kubernetes import (
    KubernetesUpstreamClient,
    prune_k8s_manifest,
    sanitize_manifest_content,
)

SAMPLE_RAW_POD_YAML = """
apiVersion: v1
kind: Pod
metadata:
  name: test-pod
  namespace: default
  uid: "3b036512-5eb3-4fe8-8c1d-111111111111"
  resourceVersion: "1234567"
  generation: 4
  managedFields:
  - manager: kube-controller-manager
    operation: Update
    time: "2026-10-01T12:00:00Z"
  - manager: kubectl
    operation: Apply
    time: "2026-10-02T14:00:00Z"
  annotations:
    kubectl.kubernetes.io/last-applied-configuration: '{"apiVersion":"v1","kind":"Pod","metadata":{"name":"test-pod"}}'
    app.kubernetes.io/version: "1.2.3"
spec:
  containers:
  - name: test-container
    image: nginx:latest
status:
  phase: Running
"""

SAMPLE_RAW_POD_JSON = """{
  "apiVersion": "v1",
  "kind": "Pod",
  "metadata": {
    "name": "json-pod",
    "namespace": "production",
    "uid": "abc-123",
    "resourceVersion": "987654",
    "generation": 2,
    "managedFields": [{"manager": "argocd"}],
    "annotations": {
      "kubectl.kubernetes.io/last-applied-configuration": "{}",
      "example.com/tier": "frontend"
    }
  },
  "spec": {
    "replicas": 1
  }
}"""


def test_prune_k8s_manifest_removes_noisy_fields():
    raw_dict = yaml.safe_load(SAMPLE_RAW_POD_YAML)
    pruned = prune_k8s_manifest(raw_dict)

    metadata = pruned["metadata"]
    assert "managedFields" not in metadata
    assert "uid" not in metadata
    assert "resourceVersion" not in metadata
    assert "generation" not in metadata

    annotations = metadata["annotations"]
    assert "kubectl.kubernetes.io/last-applied-configuration" not in annotations
    assert annotations["app.kubernetes.io/version"] == "1.2.3"

    assert pruned["apiVersion"] == "v1"
    assert pruned["kind"] == "Pod"
    assert metadata["name"] == "test-pod"
    assert metadata["namespace"] == "default"
    assert pruned["spec"]["containers"][0]["image"] == "nginx:latest"
    assert pruned["status"]["phase"] == "Running"


def test_prune_k8s_manifest_handles_items_list():
    raw_list_resource = {
        "apiVersion": "v1",
        "kind": "PodList",
        "items": [
            {
                "metadata": {
                    "name": "pod-1",
                    "managedFields": [{"manager": "k8s"}],
                    "uid": "uid-1",
                    "annotations": {
                        "kubectl.kubernetes.io/last-applied-configuration": "{}",
                        "keep-me": "yes",
                    },
                }
            },
            {
                "metadata": {
                    "name": "pod-2",
                    "resourceVersion": "2222",
                    "generation": 1,
                }
            },
        ],
    }

    pruned = prune_k8s_manifest(raw_list_resource)
    item1_meta = pruned["items"][0]["metadata"]
    assert "managedFields" not in item1_meta
    assert "uid" not in item1_meta
    assert "kubectl.kubernetes.io/last-applied-configuration" not in item1_meta["annotations"]
    assert item1_meta["annotations"]["keep-me"] == "yes"

    item2_meta = pruned["items"][1]["metadata"]
    assert "resourceVersion" not in item2_meta
    assert "generation" not in item2_meta


def test_sanitize_manifest_content_from_yaml():
    sanitized_yaml = sanitize_manifest_content(SAMPLE_RAW_POD_YAML)
    parsed = yaml.safe_load(sanitized_yaml)

    assert "managedFields" not in parsed["metadata"]
    assert "last-applied-configuration" not in str(parsed["metadata"].get("annotations", {}))
    assert "uid" not in parsed["metadata"]
    assert "resourceVersion" not in parsed["metadata"]
    assert "generation" not in parsed["metadata"]
    assert parsed["metadata"]["name"] == "test-pod"
    assert parsed["metadata"]["annotations"]["app.kubernetes.io/version"] == "1.2.3"


def test_sanitize_manifest_content_from_json():
    sanitized_yaml = sanitize_manifest_content(SAMPLE_RAW_POD_JSON)
    parsed = yaml.safe_load(sanitized_yaml)

    assert "managedFields" not in parsed["metadata"]
    assert "uid" not in parsed["metadata"]
    assert parsed["metadata"]["name"] == "json-pod"
    assert parsed["metadata"]["annotations"]["example.com/tier"] == "frontend"
    assert (
        "kubectl.kubernetes.io/last-applied-configuration" not in parsed["metadata"]["annotations"]
    )


def test_sanitize_manifest_content_invalid_or_plain_text():
    plain_text = "Error from server (NotFound): pods 'test' not found"
    assert sanitize_manifest_content(plain_text) == plain_text

    simple_number = "12345"
    assert sanitize_manifest_content(simple_number) == simple_number


@pytest.mark.asyncio
async def test_kubernetes_upstream_client_tools_list_and_pruning():
    client = KubernetesUpstreamClient(
        command="kubectl",
        upstream_type=UpstreamType.KUBERNETES,
        prefix="k8s_",
    )

    tools = await client.list_tools()
    tool_names = [t.name for t in tools]
    assert "k8s_kubectl" in tool_names
    assert "k8s_pods_get" in tool_names
    assert "k8s_resources_get" in tool_names
    assert "k8s_resources_create_or_update" in tool_names

    fake_result = ToolResult(
        status="success",
        content=SAMPLE_RAW_POD_YAML,
        is_error=False,
    )

    with patch.object(client, "_exec_kubectl", new_callable=AsyncMock, return_value=fake_result):
        # 1. k8s_kubectl call
        res_cmd = await client.call_tool("k8s_kubectl", {"command": "get pods -n argocd"})
        assert res_cmd.status == "success"
        client._exec_kubectl.assert_called_with(["get", "pods", "-n", "argocd"])

        # 2. k8s_resources_get call
        await client.call_tool(
            "k8s_resources_get", {"kind": "Application", "name": "bentopdf", "namespace": "argocd"}
        )
        client._exec_kubectl.assert_called_with(
            ["get", "Application/bentopdf", "-n", "argocd", "-o", "yaml"]
        )

        # 3. k8s_pods_get call
        await client.call_tool("k8s_pods_get", {"name": "test-pod", "namespace": "default"})
        client._exec_kubectl.assert_called_with(
            ["get", "pod", "test-pod", "-n", "default", "-o", "yaml"]
        )

        # 4. k8s_resources_create_or_update with dict manifest
        await client.call_tool(
            "k8s_resources_create_or_update",
            {"resource": {"apiVersion": "v1", "kind": "ConfigMap"}},
        )
        assert client._exec_kubectl.call_args[0][0] == ["apply", "-f", "-"]
        assert "kind: ConfigMap" in client._exec_kubectl.call_args[1]["stdin_data"]

        # 5. k8s_resources_patch with dict patch
        await client.call_tool(
            "k8s_resources_patch",
            {
                "kind": "Application",
                "name": "bentopdf",
                "namespace": "argocd",
                "patch": {"spec": {"syncPolicy": {"automated": {}}}},
            },
        )
        client._exec_kubectl.assert_called_with(
            [
                "patch",
                "Application/bentopdf",
                "-n",
                "argocd",
                "--type=merge",
                "-p",
                '{"spec": {"syncPolicy": {"automated": {}}}}',
            ]
        )


@pytest.mark.asyncio
async def test_kubernetes_upstream_client_error_handling():
    client = KubernetesUpstreamClient(
        command="kubectl",
        upstream_type=UpstreamType.KUBERNETES,
        prefix="k8s_",
    )

    err_content = "Error from server (NotFound): pods 'test' not found"
    err_result = ToolResult(status="failed", content=err_content, is_error=True)

    with patch.object(client, "_exec_kubectl", new_callable=AsyncMock, return_value=err_result):
        res = await client.call_tool("k8s_kubectl", {"command": "get pod test"})
        assert res.is_error is True
        assert res.content == err_content
