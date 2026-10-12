"""Domain specialist agent implementation for LYOKO."""

import logging
from typing import Any

from lyoko.application.context.temporal import build_temporal_context
from lyoko.config import settings
from lyoko.domain.interfaces.llm import LLMClientInterface
from lyoko.domain.interfaces.mcp import MCPClientInterface
from lyoko.domain.interfaces.specialist import SpecialistAgentInterface

logger = logging.getLogger("lyoko.specialists")


class SpecialistToolCatalogFormatter:
    """Formats upstream domain tool catalogs for injection into specialist system prompts."""

    _MAX_ENUM_ITEMS: int = 8

    @classmethod
    def _format_default(cls, val: Any) -> str:
        if isinstance(val, bool):
            return f"={str(val).lower()}"
        if isinstance(val, str):
            return f"='{val}'"
        if isinstance(val, (int, float)):
            return f"={val}"
        return ""

    @classmethod
    def _format_enum(cls, enum_vals: list[Any]) -> str:
        if not enum_vals:
            return ""
        formatted = [f"'{v}'" if isinstance(v, str) else str(v) for v in enum_vals]
        if len(formatted) > cls._MAX_ENUM_ITEMS:
            return f"[{'|'.join(formatted[:6])}|...]"
        return f"[{'|'.join(formatted)}]"

    @classmethod
    def format_signature(cls, name: str, parameters: Any) -> str:
        """Render a compact call signature (name, argument names, defaults, and enums)."""
        if not isinstance(parameters, dict):
            return f"{name}()"
        properties = parameters.get("properties")
        if not isinstance(properties, dict) or not properties:
            return f"{name}()"
        required = parameters.get("required")
        required_set = set(required) if isinstance(required, list) else set()

        args: list[str] = []
        for arg, prop_schema in properties.items():
            arg_str = arg if arg in required_set else f"{arg}?"
            if isinstance(prop_schema, dict):
                default_val = prop_schema.get("default")
                if default_val is not None:
                    arg_str += cls._format_default(default_val)
                enum_vals = prop_schema.get("enum")
                if isinstance(enum_vals, list) and enum_vals:
                    arg_str += cls._format_enum(enum_vals)
            args.append(arg_str)
        return f"{name}({', '.join(args)})"

    @classmethod
    def format_catalog(cls, catalog: list[Any]) -> str:
        """Render a domain tool index as a prompt section, or an empty string when unavailable."""
        lines: list[str] = []
        for entry in catalog:
            if not isinstance(entry, dict):
                continue
            name = entry.get("name")
            if not name:
                continue
            signature = cls.format_signature(str(name), entry.get("parameters"))
            description = str(entry.get("description") or "").strip()
            lines.append(f"- {signature}: {description}" if description else f"- {signature}")
        if not lines:
            return ""
        body = "\n".join(lines)
        return (
            "--- AVAILABLE TOOLS IN YOUR DOMAIN ---\n"
            "Call them by passing `tool_name` and `arguments` to `gateway_call_tool(tool_name, arguments)`.\n"
            'Example: `gateway_call_tool(tool_name="<tool_name>", arguments={...})`. The `tool_name` is REQUIRED.\n'
            "The signature after each name shows its exact argument names ('?' = optional). Do NOT call "
            "`gateway_get_tool_schema` upfront; all argument names are already listed below. Only call "
            "`gateway_get_tool_schema` if a `gateway_call_tool` invocation fails or is rejected.\n"
            f"{body}\n"
            "---------------------------------------"
        )


class DomainSpecialistAgent(SpecialistAgentInterface):
    """Specialist subagent focused on a single domain (e.g. unifi, kubernetes, homeassistant, grafana)."""

    def __init__(
        self,
        name: str,
        domain: str,
        system_prompt: str,
        llm: LLMClientInterface | None = None,
        mcp_client: MCPClientInterface | None = None,
        tools: list[Any] | None = None,
        max_steps: int | None = None,
    ) -> None:
        self._name = name
        self._domain = domain
        self.system_prompt = system_prompt
        self.llm = llm
        self.mcp_client = mcp_client
        self.tools = tools or []
        self._max_steps = max_steps or settings.max_agent_steps
        self._cached_tool_index: str | None = None

    @property
    def name(self) -> str:
        return self._name

    @property
    def domain(self) -> str:
        return self._domain

    async def get_tools(self, authorizer: Any = None) -> list[Any]:
        """Resolve tools scoped to this specialist's domain."""
        if self.tools:
            return self.tools
        if self.mcp_client is not None and hasattr(self.mcp_client, "get_domain_langchain_tools"):
            return await self.mcp_client.get_domain_langchain_tools(
                self.domain,
                authorizer=authorizer,
            )
        return []

    async def get_system_prompt(self) -> str:
        """Return the base prompt with this domain's tool index and temporal context injected."""
        parts = [self.system_prompt, build_temporal_context()]
        index = await self._domain_tool_index()
        if index:
            parts.append(index)
        return "\n\n".join(parts)

    async def _domain_tool_index(self) -> str:
        if self._cached_tool_index is not None:
            return self._cached_tool_index
        if self.mcp_client is None or not hasattr(self.mcp_client, "get_domain_catalog"):
            self._cached_tool_index = ""
            return ""
        try:
            catalog = await self.mcp_client.get_domain_catalog(self.domain)
        except Exception as exc:  # noqa: BLE001 - degrade gracefully, tools surface the real error
            logger.warning("Could not load tool catalog for domain '%s': %s", self.domain, exc)
            self._cached_tool_index = ""
            return ""
        self._cached_tool_index = SpecialistToolCatalogFormatter.format_catalog(catalog)
        return self._cached_tool_index

    async def run(
        self,
        prompt: str,
        session_id: str | None = None,
        user_id: str | None = None,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        authorizer: Any = None,
        parent_config: Any = None,
        on_status: Any = None,
    ) -> str:
        """Execute a domain specialist query."""
        logger.info("Running specialist %s for query: %s", self.name, prompt)
        if self.llm is None:
            return f"Specialist {self.name}: LLM client not configured."

        specialist_tags = list(tags or [])
        specialist_tags.extend(["specialist", f"specialist:{self.domain}"])

        tools = await self.get_tools(authorizer=authorizer)
        system_prompt = await self.get_system_prompt()

        return await self.llm.chat(
            prompt=prompt,
            system_prompt=system_prompt,
            tools=tools if tools else None,
            session_id=session_id,
            user_id=user_id,
            trace_name=f"specialist-{self.domain}",
            tags=specialist_tags,
            metadata=metadata or {},
            max_steps=self._max_steps,
            parent_config=parent_config,
            on_status=on_status,
        )
