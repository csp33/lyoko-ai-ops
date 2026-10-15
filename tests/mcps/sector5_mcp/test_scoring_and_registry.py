"""Unit tests for tool aliases and the ToolRegistry."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from sector5_mcp.application.guardrail import GuardrailEngine
from sector5_mcp.application.registry import ToolRegistry
from sector5_mcp.domain.interfaces.upstream import UpstreamMCPInterface
from sector5_mcp.domain.models.aliases import UPSTREAM_ALIASES, resolve_canonical_domain
from sector5_mcp.domain.models.guardrail import GuardrailPolicy
from sector5_mcp.domain.models.upstream import ToolDefinition, UpstreamType


def test_resolve_canonical_domain():
    assert resolve_canonical_domain("k8s") == "kubernetes"
    assert resolve_canonical_domain("wifi") == "unifi"
    assert resolve_canonical_domain("ha") == "homeassistant"
    assert resolve_canonical_domain("gitops") == "kubernetes"
    assert resolve_canonical_domain("telegram") == "telegram"
    assert resolve_canonical_domain("unknown_custom") == "unknown_custom"
    assert "router" in UPSTREAM_ALIASES


def test_resolve_tool_alias():
    from sector5_mcp.domain.models.aliases import resolve_tool_alias

    assert resolve_tool_alias("kubernetes_resources_get") == "k8s_resources_get"
    assert resolve_tool_alias("kubernetes_pods_get") == "k8s_pods_get"
    assert resolve_tool_alias("k8s_get_resources") == "k8s_resources_get"
    assert resolve_tool_alias("k8s_get_pod") == "k8s_pods_get"
    assert resolve_tool_alias("k8s_get_application") == "k8s_resources_get"
    assert resolve_tool_alias("kubernetes_get_application") == "k8s_resources_get"
    assert resolve_tool_alias("argocd_get_application") == "k8s_resources_get"
    assert resolve_tool_alias("k8s_patch_namespaced_custom_object") == "k8s_resources_create_or_update"
    assert resolve_tool_alias("k8s_create_namespaced_custom_object") == "k8s_resources_create_or_update"
    assert resolve_tool_alias("unifi_get_devices") == "unifi_list_devices"
    assert resolve_tool_alias("grafana_query") == "grafana_query_prometheus"
    assert resolve_tool_alias("grafana_data_query") == "grafana_query_prometheus"
    assert resolve_tool_alias("grafana_get_datasources") == "grafana_list_datasources"
    assert resolve_tool_alias("unmodified_tool_name") == "unmodified_tool_name"


@pytest.mark.asyncio
async def test_tool_registry_discovery_and_routing():
    guardrail = GuardrailEngine(GuardrailPolicy())
    mock_ha = MagicMock(spec=UpstreamMCPInterface)
    mock_ha.list_tools = AsyncMock(
        return_value=[
            ToolDefinition(
                name="ha_get_state",
                description="Get state of an entity",
                upstream_type=UpstreamType.HOME_ASSISTANT,
            )
        ]
    )

    registry = ToolRegistry(
        upstreams={UpstreamType.HOME_ASSISTANT: mock_ha},
        guardrail=guardrail,
    )

    tools = await registry.discover_tools()
    assert len(tools) == 1
    assert tools[0].name == "ha_get_state"

    # Domain lookup with an alias resolves to the same catalog
    domain_tools = await registry.get_domain_tools("iot")
    assert [t.name for t in domain_tools] == ["ha_get_state"]
    assert domain_tools == await registry.get_domain_tools("homeassistant")

    # Route resolution
    route = registry.resolve_tool_routing("ha_get_state")
    assert route is not None
    resolved_name, upstream_type, client = route
    assert resolved_name == "ha_get_state"
    assert upstream_type == UpstreamType.HOME_ASSISTANT
    assert client == mock_ha


@pytest.mark.asyncio
async def test_tool_registry_resolves_grafana_dot_notation_and_normalizes_args():
    from sector5_mcp.application.use_cases.execute_tool import ExecuteToolUseCase

    guardrail = GuardrailEngine(GuardrailPolicy())
    mock_grafana = MagicMock(spec=UpstreamMCPInterface)
    mock_grafana.list_tools = AsyncMock(
        return_value=[
            ToolDefinition(
                name="grafana_query_prometheus",
                description="Query Prometheus metrics",
                upstream_type=UpstreamType.GRAFANA,
            )
        ]
    )
    mock_grafana.call_tool = AsyncMock(return_value={"status": "success"})

    registry = ToolRegistry(
        upstreams={UpstreamType.GRAFANA: mock_grafana},
        guardrail=guardrail,
    )
    await registry.discover_tools()

    # 1. Dot-notation resolves via alias to canonical tool name
    route = registry.resolve_tool_routing("grafana.query")
    assert route is not None
    assert route[0] == "grafana_query_prometheus"

    # 2. ExecuteToolUseCase normalizes query -> expr, datasourceUid, endTime
    use_case = ExecuteToolUseCase(registry, guardrail)
    await use_case.execute("grafana.query", {"query": "up", "step": "30s"})

    mock_grafana.call_tool.assert_called_once_with(
        "grafana_query_prometheus",
        {
            "expr": "up",
            "datasourceUid": "prometheus",
            "endTime": "now",
            "queryType": "instant",
            "stepSeconds": 30,
        },
    )


@pytest.mark.asyncio
async def test_tool_registry_resolves_kubernetes_application_and_normalizes_args():
    from sector5_mcp.application.use_cases.execute_tool import ExecuteToolUseCase

    guardrail = GuardrailEngine(GuardrailPolicy())
    mock_k8s = MagicMock(spec=UpstreamMCPInterface)
    mock_k8s.list_tools = AsyncMock(
        return_value=[
            ToolDefinition(
                name="k8s_resources_get",
                description="Get a Kubernetes resource",
                upstream_type=UpstreamType.KUBERNETES,
            ),
            ToolDefinition(
                name="k8s_resources_create_or_update",
                description="Create or update a Kubernetes resource",
                upstream_type=UpstreamType.KUBERNETES,
            ),
        ]
    )
    mock_k8s.call_tool = AsyncMock(return_value={"status": "success"})

    registry = ToolRegistry(
        upstreams={UpstreamType.KUBERNETES: mock_k8s},
        guardrail=guardrail,
    )
    await registry.discover_tools()

    # 1. Dot-notation kubernetes.get_application resolves to k8s_resources_get
    route = registry.resolve_tool_routing("kubernetes.get_application")
    assert route is not None
    assert route[0] == "k8s_resources_get"

    # 2. ExecuteToolUseCase normalizes name and defaults kind/apiVersion/namespace for Argo CD
    use_case = ExecuteToolUseCase(registry, guardrail)
    await use_case.execute(
        "kubernetes.get_application", {"name": "bentopdf", "namespace": "argocd"}
    )

    mock_k8s.call_tool.assert_called_once_with(
        "k8s_resources_get",
        {
            "name": "bentopdf",
            "namespace": "argocd",
            "kind": "Application",
            "apiVersion": "argoproj.io/v1alpha1",
        },
    )

    # 3. Direct k8s_resources_get with snake_case api_version normalizes to camelCase apiVersion
    mock_k8s.call_tool.reset_mock()
    await use_case.execute(
        "k8s_resources_get",
        {
            "api_version": "argoproj.io/v1alpha1",
            "kind": "Application",
            "name": "bentopdf",
            "namespace": "argocd",
        },
    )
    # 4. Custom object patch alias normalization to k8s_resources_create_or_update
    import yaml

    mock_k8s.call_tool.reset_mock()
    await use_case.execute(
        "k8s_patch_namespaced_custom_object",
        {
            "group": "argoproj.io",
            "version": "v1alpha1",
            "plural": "applications",
            "namespace": "argocd",
            "name": "bentopdf",
            "body": {
                "spec": {
                    "syncPolicy": {
                        "automated": {"prune": True, "selfHeal": True}
                    }
                }
            },
        },
    )
    mock_k8s.call_tool.assert_called_once()
    called_tool, called_args = mock_k8s.call_tool.call_args[0]
    assert called_tool == "k8s_resources_create_or_update"
    assert "resource" in called_args
    parsed_yaml = yaml.safe_load(called_args["resource"])
    assert parsed_yaml["apiVersion"] == "argoproj.io/v1alpha1"
    assert parsed_yaml["kind"] == "Application"
    assert parsed_yaml["metadata"]["name"] == "bentopdf"
    assert parsed_yaml["metadata"]["namespace"] == "argocd"
    assert parsed_yaml["spec"]["syncPolicy"]["automated"]["prune"] is True
