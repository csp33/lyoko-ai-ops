"""OpenAI / LangChain LLM infrastructure adapter for LYOKO."""

import logging
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import RunnableLambda
from langchain_openai import ChatOpenAI
from lyoko.domain.interfaces.harness import HarnessRunnerInterface
from lyoko.domain.interfaces.llm import LLMClientInterface
from lyoko.infrastructure.llm.content import extract_message_text
from lyoko.infrastructure.llm.harness_runner import ReActHarnessRunner
from lyoko.infrastructure.llm.tracing import child_config
from lyoko.infrastructure.observability.langfuse import get_langfuse_trace_config

logger = logging.getLogger("lyoko.infrastructure.llm.openai")


class OpenAILLMAdapter(LLMClientInterface):
    """LLM adapter implementing LLMClientInterface backed by ChatOpenAI and LangChain."""

    def __init__(
        self,
        api_key: str | None = None,
        model_name: str = "gpt-4o-mini",
        temperature: float = 0.0,
        use_responses_api: bool = True,
        harness_runner: HarnessRunnerInterface | None = None,
    ) -> None:
        self.model_name = model_name
        self.temperature = temperature
        self.use_responses_api = use_responses_api
        self._api_key = api_key
        self._client: ChatOpenAI | None = None
        self._harness_runner = harness_runner or ReActHarnessRunner()

    @property
    def client(self) -> ChatOpenAI:
        if self._client is None:
            api_key = self._api_key or "sk-dummy"
            self._client = ChatOpenAI(
                model=self.model_name,
                temperature=self.temperature,
                api_key=api_key,
                use_responses_api=self.use_responses_api,
            )
        return self._client

    async def chat(
        self,
        prompt: str,
        system_prompt: str | None = None,
        tools: list[Any] | None = None,
        session_id: str | None = None,
        user_id: str | None = None,
        trace_name: str | None = None,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        max_steps: int | None = None,
        parent_config: dict[str, Any] | None = None,
        on_status: Any = None,
    ) -> str:
        """Process conversational prompt with optional tools, Langfuse session, user, and tracing.

        ``max_steps`` bounds the tool-use iterations of the ReAct agent. When exceeded, the
        underlying loop raises instead of looping forever.

        With ``parent_config`` the call joins the trace of the enclosing graph run as a child
        span named ``trace_name``. Without it, a new trace is started.

        ``on_status`` is an optional async callback invoked with a short, factual status (such as
        the tool or specialist actually running) so the operator can see live progress.
        """
        if parent_config is not None:
            config = child_config(parent_config, trace_name, tags, metadata)
        else:
            config = get_langfuse_trace_config(
                session_id=session_id,
                user_id=user_id,
                trace_name=trace_name or "telegram-chat-interaction",
                tags=tags or ["telegram", "chat-agent"],
                metadata=metadata,
            )

        if tools:
            tools_by_name = {tool.name: tool for tool in tools}
            model_with_tools = self.client.bind_tools(tools)
            max_iterations = max_steps or 10

            resolved_trace_id = None
            if metadata and isinstance(metadata, dict):
                resolved_trace_id = metadata.get("trace_id")
            if not resolved_trace_id and parent_config and isinstance(parent_config, dict):
                resolved_trace_id = parent_config.get("metadata", {}).get(
                    "trace_id"
                ) or parent_config.get("configurable", {}).get("trace_id")
                if not resolved_trace_id:
                    raw_cbs = parent_config.get("callbacks")
                    cbs = getattr(raw_cbs, "handlers", raw_cbs) if raw_cbs is not None else []
                    if isinstance(cbs, (list, tuple)):
                        for cb in cbs:
                            if hasattr(cb, "trace_context") and getattr(
                                cb.trace_context, "trace_id", None
                            ):
                                resolved_trace_id = cb.trace_context.trace_id
                                break
            if not resolved_trace_id and config and isinstance(config, dict):
                resolved_trace_id = config.get("metadata", {}).get("trace_id") or config.get(
                    "metadata", {}
                ).get("langfuse_trace_id")

            async def _react_loop(prompt_text: str) -> str:
                return await self._harness_runner.run(
                    prompt_text,
                    system_prompt=system_prompt,
                    model_with_tools=model_with_tools,
                    summary_client=self.client,
                    tools_by_name=tools_by_name,
                    max_iterations=max_iterations,
                    session_id=session_id,
                    trace_id=resolved_trace_id,
                    on_status=on_status,
                )

            try:
                loop_runnable = RunnableLambda(_react_loop, name=trace_name or "chat-agent")
                return await loop_runnable.ainvoke(prompt, config=config if config else None)
            except Exception as e:
                # Never fall back to a tool-less chat: the system prompt forbids answering about
                # live infrastructure without tools, so a silent fallback produces hallucinations.
                logger.error("Error executing ReAct agent with tools: %s", e, exc_info=True)
                raise

        messages: list[Any] = []
        if system_prompt:
            messages.append(SystemMessage(content=system_prompt))
        messages.append(HumanMessage(content=prompt))

        response = await self.client.ainvoke(messages, config=config if config else None)
        return extract_message_text(response.content)
