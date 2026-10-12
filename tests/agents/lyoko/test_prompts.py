"""Unit tests for markdown prompt loading and prompt integrity."""

import pytest
from lyoko.application.agents.specialist_prompts import SpecialistPromptProvider
from lyoko.application.agents.supervisor import SUPERVISOR_SYSTEM_PROMPT
from lyoko.application.prompts.chat import CHAT_SYSTEM_PROMPT
from lyoko.application.prompts.incident import (
    DIAGNOSE_SYSTEM_PROMPT,
    REMEDIATE_SYSTEM_PROMPT,
    VERIFY_SYSTEM_PROMPT,
)
from lyoko.application.prompts.loader import load_prompt
from lyoko.application.prompts.router import ROUTER_SYSTEM_PROMPT

K8S_SPECIALIST_PROMPT = SpecialistPromptProvider.get_prompt("k8s")
NETWORK_SPECIALIST_PROMPT = SpecialistPromptProvider.get_prompt("network")
SMARTHOME_SPECIALIST_PROMPT = SpecialistPromptProvider.get_prompt("smarthome")
OBSERVABILITY_SPECIALIST_PROMPT = SpecialistPromptProvider.get_prompt("observability")


def test_load_prompt_success():
    chat_prompt = load_prompt("chat.md")
    assert "LYOKO" in chat_prompt
    assert "AIOps" in chat_prompt


def test_load_prompt_missing_file():
    with pytest.raises(FileNotFoundError, match="Prompt file not found"):
        load_prompt("nonexistent_prompt.md")


def test_all_prompts_loaded_properly():
    assert "LYOKO" in CHAT_SYSTEM_PROMPT
    assert "READ-ONLY" in DIAGNOSE_SYSTEM_PROMPT
    assert "RESULT:" in REMEDIATE_SYSTEM_PROMPT
    assert "RESOLVED or UNRESOLVED" in VERIFY_SYSTEM_PROMPT
    assert "INCIDENT" in ROUTER_SYSTEM_PROMPT
    assert "Supervisor" in SUPERVISOR_SYSTEM_PROMPT
    assert "Kubernetes" in K8S_SPECIALIST_PROMPT
    assert "UniFi" in NETWORK_SPECIALIST_PROMPT
    assert "Home Assistant" in SMARTHOME_SPECIALIST_PROMPT
    assert (
        "Grafana" in OBSERVABILITY_SPECIALIST_PROMPT
        or "Prometheus" in OBSERVABILITY_SPECIALIST_PROMPT
    )


def test_supervisor_prompt_guards_against_hijack_and_domain_substitution():
    assert "operator's latest message" in SUPERVISOR_SYSTEM_PROMPT.lower()
    assert "Do not substitute a different domain" in SUPERVISOR_SYSTEM_PROMPT
    assert "at most once" in SUPERVISOR_SYSTEM_PROMPT


def test_smarthome_prompt_forbids_invented_tool_names():
    assert "Never invent or guess a tool name" in SMARTHOME_SPECIALIST_PROMPT


def test_observability_prompt_requires_datasource_uid():
    assert "datasourceUid" in OBSERVABILITY_SPECIALIST_PROMPT
    assert "datasource or UID error" in OBSERVABILITY_SPECIALIST_PROMPT


def test_supervisor_routes_hardware_metrics_to_grafana():
    assert "node-exporter" in SUPERVISOR_SYSTEM_PROMPT
