"""Unit tests for SupervisorAgent multi-agent coordination."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from lyoko.application.agents.supervisor import SUPERVISOR_SYSTEM_PROMPT, SupervisorAgent
from lyoko.domain.interfaces.llm import LLMClientInterface


@pytest.mark.asyncio
async def test_supervisor_initialization():
    assert "Supervisor" in SUPERVISOR_SYSTEM_PROMPT
    assert "specialist" in SUPERVISOR_SYSTEM_PROMPT.lower()


@pytest.mark.asyncio
async def test_supervisor_coordinates_multi_step():
    mock_k8s = MagicMock()
    mock_k8s.run = AsyncMock(return_value="Pod memory updated to 512Mi")
    mock_ha = MagicMock()
    mock_ha.run = AsyncMock(return_value="Zigbee integration reloaded successfully")

    mock_llm = MagicMock(spec=LLMClientInterface)
    mock_llm.chat = AsyncMock(
        return_value="All actions completed: Pod scaled to 512Mi and Zigbee integration reloaded."
    )

    supervisor = SupervisorAgent(
        specialists={
            "kubernetes": mock_k8s,
            "homeassistant": mock_ha,
        },
        llm=mock_llm,
    )

    result = await supervisor.coordinate(
        prompt="Scale pod zigbee2mqtt memory and reload zigbee in HA",
        session_id="test-session",
    )
    assert "completed" in result.lower()
    mock_llm.chat.assert_awaited_once()


@pytest.mark.asyncio
async def test_supervisor_delegate_to_specialist():
    mock_unifi = MagicMock()
    mock_unifi.run = AsyncMock(return_value="Found 45 connected WiFi clients")

    supervisor = SupervisorAgent(
        specialists={"unifi": mock_unifi},
    )

    res = await supervisor.delegate("unifi", "List all clients")
    assert res == "Found 45 connected WiFi clients"
    mock_unifi.run.assert_awaited_once_with(
        prompt="List all clients",
        session_id=None,
        user_id=None,
        tags=None,
        metadata=None,
        authorizer=None,
        parent_config=None,
        on_status=None,
    )

    missing_res = await supervisor.delegate("unknown", "List all clients")
    assert "No specialist registered" in missing_res


@pytest.mark.asyncio
async def test_supervisor_builds_delegation_tools_and_uses_them_by_default():
    mock_k8s = MagicMock()
    mock_k8s.run = AsyncMock(return_value="Application is OutOfSync")
    mock_llm = MagicMock(spec=LLMClientInterface)
    mock_llm.chat = AsyncMock(return_value="Root cause found via specialist.")

    supervisor = SupervisorAgent(
        specialists={"kubernetes": mock_k8s},
        llm=mock_llm,
    )

    tools = supervisor.get_delegation_tools()
    assert len(tools) == 1
    assert tools[0].name == "ask_kubernetes_specialist"

    result = await supervisor.coordinate(prompt="Why is gambling-song-staging OutOfSync?")
    assert "specialist" in result.lower()
    call_kwargs = mock_llm.chat.await_args.kwargs
    assert call_kwargs["tools"][0].name == "ask_kubernetes_specialist"
    assert "gateway_get_domain_tools" not in [t.name for t in call_kwargs["tools"]]
