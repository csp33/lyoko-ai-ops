"""Multi-Agent Supervisor and Cross-Domain Orchestrator for LYOKO."""

import logging
from typing import Any

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import StructuredTool
from lyoko.application.context.loader import build_agent_context
from lyoko.application.prompts.loader import load_prompt
from lyoko.application.safety.tool_gate import ToolGate
from lyoko.config import settings
from lyoko.domain.interfaces.llm import LLMClientInterface
from lyoko.domain.interfaces.supervisor import SupervisorInterface

logger = logging.getLogger("lyoko.supervisor")

SUPERVISOR_SYSTEM_PROMPT = f"{load_prompt('supervisor.md')}\n\n{build_agent_context('supervisor')}"

_DOMAIN_DESCRIPTIONS = {
    "kubernetes": (
        "Kubernetes SRE: pods, deployments, events, logs, generic resources including Argo CD "
        "Application CRDs, scaling, and rollouts."
    ),
    "unifi": (
        "UniFi network: clients, access points, switches, VLANs, bandwidth, DPI, and Wi-Fi health."
    ),
    "homeassistant": (
        "Smart home: Home Assistant entities, devices, automations, climate, lights, and integrations."
    ),
    "grafana": (
        "Observability: Grafana dashboards, Prometheus metrics, alert history, and datasources."
    ),
}


class SupervisorAgent(SupervisorInterface):
    """Orchestrates domain specialist subagents and plans multi-step cross-domain tasks."""

    def __init__(
        self,
        specialists: dict[str, Any] | None = None,
        llm: LLMClientInterface | None = None,
    ) -> None:
        self.specialists = specialists or {}
        self.llm = llm

    def get_specialist(self, domain: str) -> Any | None:
        """Retrieve a specialist by domain name."""
        return self.specialists.get(domain)

    async def delegate(
        self,
        domain: str,
        prompt: str,
        session_id: str | None = None,
        user_id: str | None = None,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        authorizer: Any = None,
        parent_config: Any = None,
        on_status: Any = None,
    ) -> str:
        """Delegate a task directly to a specific domain specialist."""
        specialist = self.get_specialist(domain)
        if specialist is None:
            return f"Supervisor: No specialist registered for domain '{domain}'."
        logger.info("Supervisor delegating to %s specialist: %s", domain, prompt)
        return await specialist.run(
            prompt=prompt,
            session_id=session_id,
            user_id=user_id,
            tags=tags,
            metadata=metadata,
            authorizer=authorizer,
            parent_config=parent_config,
            on_status=on_status,
        )

    def get_delegation_tools(
        self,
        *,
        authorizer: Any = None,
        session_id: str | None = None,
        user_id: str | None = None,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        parent_config: Any = None,
        on_status: Any = None,
    ) -> list[Any]:
        """Return one LangChain tool per registered specialist for supervisor delegation."""
        tools: list[Any] = []
        for domain in self.specialists:
            description = _DOMAIN_DESCRIPTIONS.get(
                domain,
                f"Domain specialist for '{domain}'.",
            )

            async def _ask(
                task: str,
                _domain: str = domain,
            ) -> str:
                return await self.delegate(
                    _domain,
                    task,
                    session_id=session_id,
                    user_id=user_id,
                    tags=tags,
                    metadata=metadata,
                    authorizer=authorizer,
                    parent_config=parent_config,
                    on_status=on_status,
                )

            tools.append(
                StructuredTool.from_function(
                    coroutine=_ask,
                    name=f"ask_{domain}_specialist",
                    description=(
                        f"Delegate a concrete task to the {domain} specialist. {description} "
                        "Pass a clear task with resource names, namespaces, and the expected outcome. "
                        "Do not call gateway discovery tools; specialists own their domain toolsets."
                    ),
                    handle_tool_error=True,
                )
            )
        return tools

    async def coordinate(
        self,
        prompt: str,
        session_id: str | None = None,
        user_id: str | None = None,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        tools: list[Any] | None = None,
        system_prompt: str | None = None,
        authorizer: Any = None,
        parent_config: Any = None,
        max_steps: int | None = None,
        on_status: Any = None,
    ) -> str:
        """Coordinate multi-agent task execution by delegating to domain specialists."""
        logger.info("Supervisor coordinating request: %s", prompt)
        if self.llm is None:
            return f"Supervisor: LLM client not configured to process '{prompt}'."

        supervisor_tags = list(tags or [])
        supervisor_tags.extend(["supervisor", "multi-agent"])

        delegation_tools = tools
        if delegation_tools is None:
            delegation_tools = self.get_delegation_tools(
                authorizer=authorizer,
                session_id=session_id,
                user_id=user_id,
                tags=supervisor_tags,
                metadata=metadata,
                parent_config=parent_config,
                on_status=on_status,
            )

        return await self.llm.chat(
            prompt=prompt,
            system_prompt=system_prompt or SUPERVISOR_SYSTEM_PROMPT,
            tools=delegation_tools,
            session_id=session_id,
            user_id=user_id,
            trace_name="supervisor-coordination",
            tags=supervisor_tags,
            metadata=metadata or {},
            max_steps=max_steps or settings.max_supervisor_steps,
            parent_config=parent_config,
            on_status=on_status,
        )


class SupervisedAgentRunner:
    """Orchestrates agent execution through a supervisor or standalone agent with ToolGate."""

    @staticmethod
    def extract_status_callback(config: Any) -> Any:
        """Return the streaming status callback carried by the run config, if any."""
        if not isinstance(config, dict):
            return None
        return config.get("configurable", {}).get("on_status")

    @classmethod
    async def run_agent(
        cls,
        state: dict[str, Any],
        gate: ToolGate,
        config: RunnableConfig,
        mcp_client: Any,
        llm: LLMClientInterface | None,
        *,
        phase: str,
        system_prompt: str,
        prompt: str,
        on_status: Any = None,
    ) -> str:
        """Run one tool-using agent with every tool call going through ``gate``."""
        assert llm is not None
        kwargs: dict[str, Any] = {
            "prompt": prompt,
            "system_prompt": system_prompt,
            "tools": mcp_client.get_langchain_tools(authorizer=gate.authorize),
            "session_id": state.get("session_id"),
            "trace_name": f"{phase}-agent",
            "tags": [f"phase:{phase}"],
            "max_steps": settings.max_agent_steps,
            "parent_config": config,
        }
        if on_status is not None:
            kwargs["on_status"] = on_status
        return await llm.chat(**kwargs)

    @classmethod
    async def run_supervised(
        cls,
        state: dict[str, Any],
        gate: ToolGate,
        config: RunnableConfig,
        mcp_client: Any,
        llm: LLMClientInterface | None,
        supervisor: Any,
        *,
        phase: str,
        prompt: str,
        system_prompt: str | None = None,
        on_status: Any = None,
    ) -> str:
        """Run through the supervisor + specialists when available; otherwise fall back."""
        if supervisor is None:
            if system_prompt is None:
                raise ValueError("system_prompt is required when no supervisor is configured")
            return await cls.run_agent(
                state,
                gate,
                config,
                mcp_client=mcp_client,
                llm=llm,
                phase=phase,
                system_prompt=system_prompt,
                prompt=prompt,
                on_status=on_status,
            )
        return await supervisor.coordinate(
            prompt=prompt,
            system_prompt=system_prompt,
            session_id=state.get("session_id"),
            tags=[f"phase:{phase}"],
            authorizer=gate.authorize,
            parent_config=config,
            max_steps=settings.max_agent_steps,
            on_status=on_status,
        )


run_supervised = SupervisedAgentRunner.run_supervised
run_agent = SupervisedAgentRunner.run_agent
status_callback = SupervisedAgentRunner.extract_status_callback
