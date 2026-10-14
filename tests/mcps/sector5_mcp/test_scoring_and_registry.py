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
    assert resolve_tool_alias("unifi_get_devices") == "unifi_list_devices"
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
