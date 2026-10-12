"""LangChain tool bindings for the MCP gateway.

Builds the discovery trio (list a domain, read a tool schema, execute a tool) that specialists and
the supervisor use. Each execution is optionally gated by a ``ToolAuthorizer`` before it runs.
"""

import logging
from typing import Any

from langchain_core.runnables import RunnableLambda
from langchain_core.tools import StructuredTool, ToolException
from lyoko.domain.exceptions.mcp import MCPGatewayError
from lyoko.domain.interfaces.mcp import MCPClientInterface, ToolAuthorizer
from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger("lyoko.infrastructure.mcp.langchain_tools")

_DOMAIN_ARG_HELP = "kubernetes, unifi, homeassistant, grafana, github, telegram"


class _CallToolArgs(BaseModel):
    """Permissive schema for ``gateway_call_tool``.

    Models frequently emit the target tool's parameters as top-level siblings of ``tool_name``
    instead of nesting them under ``arguments``, or emit aliases like ``name`` / ``tool``.
    Extra keys are accepted here and merged into ``arguments`` so either shape executes the
    intended call instead of failing validation.
    """

    model_config = ConfigDict(extra="allow")

    tool_name: str | None = Field(
        default=None,
        description="The exact name of the tool to execute from the domain tool catalog (e.g., 'ha_get_logs', 'k8s_get_pods').",
    )
    arguments: dict[str, Any] = Field(
        default_factory=dict,
        description="Key-value arguments to pass to the target tool.",
    )


async def _resolve_tool_name(
    client: MCPClientInterface,
    tool_name: str | None,
    merged: dict[str, Any],
    domain: str | None,
) -> tuple[str | None, dict[str, Any]]:
    # 1. Direct tool_name provided
    if tool_name:
        return tool_name, merged

    # 2. Common aliases for tool name
    for alias in ("tool", "name", "target_tool", "function"):
        if alias in merged and isinstance(merged[alias], str):
            val = merged.pop(alias)
            return val, merged

    # 3. Single key matching a nested dictionary: { "ha_get_logs": { "source": "core" } }
    if len(merged) == 1:
        only_key, only_val = next(iter(merged.items()))
        if isinstance(only_val, dict):
            return only_key, only_val

    # 4. Infer from domain tool catalog if available
    if domain and hasattr(client, "get_domain_catalog"):
        try:
            catalog = await client.get_domain_catalog(domain)
            passed_keys = set(merged.keys())
            candidates = []
            for entry in catalog:
                if not isinstance(entry, dict):
                    continue
                t_name = entry.get("name")
                params = entry.get("parameters")
                if not t_name or not isinstance(params, dict):
                    continue
                props = params.get("properties")
                if isinstance(props, dict) and passed_keys and passed_keys.issubset(props.keys()):
                    candidates.append(t_name)
            if len(candidates) == 1:
                logger.info(
                    "Inferred missing tool_name='%s' for domain '%s' from arguments: %s",
                    candidates[0],
                    domain,
                    passed_keys,
                )
                return candidates[0], merged
        except Exception as exc:  # noqa: BLE001
            logger.debug("Failed to query domain catalog for tool_name inference: %s", exc)

    return None, merged


def _authorized_call(
    client: MCPClientInterface,
    tool_name: str,
    authorizer: ToolAuthorizer | None,
):
    async def _run(arguments: dict[str, Any]) -> str:
        if authorizer is not None:
            refusal = await authorizer(tool_name, arguments)
            if refusal:
                raise ToolException(refusal)
        result = await client.call_tool(tool_name, arguments)
        if isinstance(result, dict):
            if result.get("status") == "failed":
                raise ToolException(str(result.get("error", "unknown MCP gateway error")))
            if result.get("status") == "success":
                content = result.get("content")
                if content is None or content == "" or content == [] or content == {}:
                    result = dict(result)
                    result["content"] = "No resources found or empty result."
        elif result is None or result == "":
            result = "No resources found or empty result."
        return str(result)

    return _run


def _build_call_tool(
    client: MCPClientInterface,
    authorizer: ToolAuthorizer | None,
    description: str,
    domain: str | None = None,
) -> StructuredTool:
    async def _call_tool(tool_name: str | None = None, arguments: Any = None, **extra: Any) -> str:
        # Models often flatten the target tool's parameters to the top level. Merge both shapes
        # so the intended call executes regardless of how the arguments were nested.
        merged: dict[str, Any] = {}
        if isinstance(arguments, dict):
            merged.update(arguments)
        for key, value in extra.items():
            merged.setdefault(key, value)

        resolved_name, final_args = await _resolve_tool_name(client, tool_name, merged, domain)
        if not resolved_name:
            domain_msg = f" in domain '{domain}'" if domain else ""
            raise ToolException(
                f"Missing required 'tool_name' when calling gateway_call_tool{domain_msg}. "
                f"You passed arguments: {list(merged.keys())}. "
                "You must specify the exact tool name (e.g., gateway_call_tool(tool_name='<target_tool>', arguments={...}))."
            )

        # Every call goes through one generic tool, so name the nested run after the real
        # gateway tool. Traces then show ``mcp:pods_log`` instead of an anonymous call.
        return await RunnableLambda(
            _authorized_call(client, resolved_name, authorizer),
            name=f"mcp:{resolved_name}",
        ).ainvoke(final_args)

    return StructuredTool.from_function(
        coroutine=_call_tool,
        name="gateway_call_tool",
        description=description,
        args_schema=_CallToolArgs,
        handle_tool_error=True,
    )


def _build_schema_tool(
    client: MCPClientInterface,
    description: str,
) -> StructuredTool:
    async def _get_tool_schema(tool_name: str) -> str:
        try:
            return str(await client.get_tool_schema(tool_name))
        except MCPGatewayError as exc:
            raise ToolException(str(exc)) from exc

    return StructuredTool.from_function(
        coroutine=_get_tool_schema,
        name="gateway_get_tool_schema",
        description=description,
        handle_tool_error=True,
    )


def build_gateway_tools(
    client: MCPClientInterface, authorizer: ToolAuthorizer | None = None
) -> list[Any]:
    """Tools to discover and execute homelab tools across every upstream domain."""

    async def _get_domain_tools(domain: str, limit: int = 50, offset: int = 0) -> str:
        """List one page of the tools of one upstream domain.

        Args:
            domain: Upstream domain, e.g. 'kubernetes', 'unifi', 'homeassistant', 'grafana', 'github', 'telegram'.
            limit: Maximum tools to return in this page (max 100).
            offset: Number of tools to skip for pagination.
        """
        try:
            return str(await client.get_domain_tools_page(domain, limit=limit, offset=offset))
        except MCPGatewayError as exc:
            raise ToolException(str(exc)) from exc

    domain_tool = StructuredTool.from_function(
        coroutine=_get_domain_tools,
        name="gateway_get_domain_tools",
        description=(
            f"List the tools available in one upstream domain ({_DOMAIN_ARG_HELP}). Use it to "
            "discover capabilities, then gateway_get_tool_schema and gateway_call_tool."
        ),
        handle_tool_error=True,
    )
    schema_tool = _build_schema_tool(
        client,
        description=(
            "Get the exact parameter schema of a specific tool before calling it "
            "with gateway_call_tool."
        ),
    )
    call_tool = _build_call_tool(
        client,
        authorizer,
        description=(
            "Execute any operational homelab tool by name with arguments to fetch live status, "
            "manage devices, query metrics, or perform operations. Put the target tool's "
            "parameters in the `arguments` object."
        ),
    )
    return [domain_tool, schema_tool, call_tool]


def build_domain_tools(
    client: MCPClientInterface, domain: str, authorizer: ToolAuthorizer | None = None
) -> list[Any]:
    """Domain-locked execution and schema tools for a specialist agent.

    The specialist's system prompt already injects the full domain catalog with argument
    signatures. Discovery is intentionally omitted so the model executes directly instead
    of wasting turns paginating.
    """
    call_tool = _build_call_tool(
        client,
        authorizer,
        description=(
            f"Execute a '{domain}' domain tool by exact name with arguments. The available tools "
            "and their argument names are listed in your system prompt under 'AVAILABLE TOOLS IN YOUR DOMAIN'. "
            "Put the target tool's parameters in the `arguments` object."
        ),
        domain=domain,
    )
    schema_tool = _build_schema_tool(
        client,
        description=(
            f"Get the parameter schema of one '{domain}' tool. Call only if a previous call was "
            "rejected for invalid arguments to inspect its parameters."
        ),
    )
    return [call_tool, schema_tool]
