import pytest
from lyoko.application.context.loader import (
    build_agent_context,
    get_homelab_context,
    load_context,
)
from lyoko.application.prompts.chat import CHAT_SYSTEM_PROMPT
from lyoko.application.prompts.incident import (
    DIAGNOSE_SYSTEM_PROMPT,
    REMEDIATE_SYSTEM_PROMPT,
    RESOLVE_SYSTEM_PROMPT,
    VERIFY_SYSTEM_PROMPT,
)


def test_load_context_success():
    overview = load_context("homelab_overview.md")
    assert "tresberto" in overview
    assert "humberto" in overview
    assert "dosberto" in overview
    assert "Zigbee" in overview


def test_load_context_missing_file():
    with pytest.raises(FileNotFoundError, match="Homelab context file not found"):
        load_context("nonexistent_context.md")


def test_get_homelab_context_selective():
    full_context = get_homelab_context()
    assert "HOMELAB ARCHITECTURAL CONTEXT" in full_context
    assert "tresberto" in full_context
    assert "envoy-vps-ingress" in full_context
    assert "Longhorn" in full_context

    network_only = get_homelab_context(
        include_overview=False, include_ingress=True, include_storage=False
    )
    assert "envoy-vps-ingress" in network_only
    assert "tresberto" not in network_only
    assert "ZFS RAID-1" not in network_only

    empty = get_homelab_context(
        include_overview=False, include_ingress=False, include_storage=False
    )
    assert empty == ""


def test_build_agent_context_roles():
    diag_ctx = build_agent_context("diagnose")
    assert "tresberto" in diag_ctx
    assert "envoy-vps-ingress" in diag_ctx
    assert "strategy.type: Recreate" in diag_ctx

    chat_ctx = build_agent_context("chat")
    assert "tresberto" in chat_ctx

    net_ctx = build_agent_context("network")
    assert "envoy-cloudflare-tunnel" in net_ctx
    assert "51.170.41.131" in net_ctx

    sh_ctx = build_agent_context("smarthome")
    assert "Zigbee" in sh_ctx


def test_prompts_contain_homelab_context():
    # Chat, Diagnose, Remediate, Verify, Resolve contain architectural context
    for prompt in [
        CHAT_SYSTEM_PROMPT,
        DIAGNOSE_SYSTEM_PROMPT,
        REMEDIATE_SYSTEM_PROMPT,
        VERIFY_SYSTEM_PROMPT,
        RESOLVE_SYSTEM_PROMPT,
    ]:
        assert "HOMELAB ARCHITECTURAL CONTEXT" in prompt
        assert "tresberto" in prompt
        assert "envoy-vps-ingress" in prompt
        assert "strategy.type: Recreate" in prompt
