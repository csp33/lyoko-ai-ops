"""Unit tests for the tool gate policy and the prompt answer parsers."""

import pytest
from lyoko.application.incidents.parser import IncidentOutputParser
from lyoko.application.safety.tool_gate import GateMode, ToolGate, matches_any
from lyoko.config import DEFAULT_READ_ONLY_TOOLS

parse_diagnosis = IncidentOutputParser.parse_diagnosis
parse_result = IncidentOutputParser.parse_result
parse_verdict = IncidentOutputParser.parse_verdict


def test_format_arguments_shortens_a_large_resource_manifest():
    """A full manifest argument must not flood the approval prompt with escaped YAML."""
    from lyoko.application.safety.hitl.descriptor import ApprovalActionDescriptor

    huge_manifest = "apiVersion: argoproj.io/v1alpha1\nkind: Application\n" * 50
    text = ApprovalActionDescriptor.format_arguments(
        {"resource": huge_manifest, "name": "arr-stack"}
    )

    assert "truncated" in text
    assert '"name": "arr-stack"' in text
    # The preview collapses newlines so the manifest does not render as an unreadable \\n blob.
    assert "\nkind:" not in text


def _gate(mode: GateMode = GateMode.READ_ONLY, **kwargs) -> ToolGate:
    return ToolGate(
        mode=mode,
        read_only_patterns=kwargs.pop("read_only_patterns", DEFAULT_READ_ONLY_TOOLS),
        event_id="incident-1",
        origin="Alert: TestAlert",
        **kwargs,
    )


@pytest.mark.parametrize(
    "tool",
    [
        "pods_get",
        "pods_log",
        "pods_list_in_namespace",
        "events_list",
        "nodes_top",
        "nodes_stats_summary",
        "resources_get",
        "ha_get_state",
        "ha_get_overview",
        "unifi_tool_index",
        "unifi_get_support_bundle",
        "gateway_get_domain_tools",
        "get_file_contents",
        "list_commits",
        "grafana_query_prometheus",
        "query_loki_logs",
    ],
)
def test_default_read_only_patterns_cover_inspection_tools(tool):
    assert matches_any(tool, DEFAULT_READ_ONLY_TOOLS)


@pytest.mark.parametrize(
    "tool",
    [
        "pods_delete",
        "pods_exec",
        "pods_run",
        "resources_scale",
        "resources_delete",
        "resources_create_or_update",
        "ha_call_service",
        "ha_set_entity",
        "ha_manage_app",
        "unifi_execute",
        "unifi_batch",
        "telegram_send_message",
        "create_pull_request",
    ],
)
def test_default_read_only_patterns_never_match_state_changing_tools(tool):
    assert not matches_any(tool, DEFAULT_READ_ONLY_TOOLS)


@pytest.mark.asyncio
async def test_read_only_tools_run_unrecorded_in_every_mode():
    for mode in GateMode:
        gate = _gate(mode)
        assert await gate.authorize("pods_get", {}) is None
        assert gate.records == []


@pytest.mark.asyncio
async def test_read_only_mode_refuses_everything_else_even_if_auto_approved():
    gate = _gate(GateMode.READ_ONLY, auto_approved_patterns=["resources_scale"])

    refusal = await gate.authorize("resources_scale", {"replicas": 1})

    assert refusal is not None
    assert refusal.startswith("Refused:")
    assert [r.outcome.value for r in gate.records] == ["refused"]


@pytest.mark.asyncio
async def test_upstream_read_only_hint_allows_a_tool_name_patterns_would_gate():
    """grafana_query_prometheus matches no read-only pattern; the upstream hint must unblock it."""
    gate = _gate(
        GateMode.READ_ONLY,
        readonly_lookup=lambda name: True if name == "grafana_query_prometheus" else None,
    )

    assert await gate.authorize("grafana_query_prometheus", {"expr": "up"}) is None
    assert gate.records == []


@pytest.mark.asyncio
async def test_upstream_non_read_only_hint_overrides_a_read_only_name_pattern():
    gate = _gate(
        GateMode.READ_ONLY,
        readonly_lookup=lambda name: False if name == "pods_get" else None,
    )

    refusal = await gate.authorize("pods_get", {})

    assert refusal is not None
    assert refusal.startswith("Refused:")
    assert [r.outcome.value for r in gate.records] == ["refused"]


@pytest.mark.asyncio
async def test_unknown_hint_falls_back_to_the_name_patterns():
    gate = _gate(GateMode.READ_ONLY, readonly_lookup=lambda name: None)

    assert await gate.authorize("pods_get", {}) is None


@pytest.mark.asyncio
async def test_upstream_non_read_only_hint_still_honors_auto_approval():
    gate = _gate(
        GateMode.APPROVAL,
        auto_approved_patterns=["resources_scale"],
        readonly_lookup=lambda name: False if name == "resources_scale" else None,
    )

    assert await gate.authorize("resources_scale", {"replicas": 2}) is None
    assert [r.outcome.value for r in gate.records] == ["auto_approved"]


@pytest.mark.asyncio
async def test_approval_mode_runs_auto_approved_tools_and_records_them():
    gate = _gate(GateMode.APPROVAL, auto_approved_patterns=["resources_scale", "ha_reload_*"])

    assert await gate.authorize("resources_scale", {"replicas": 1}) is None
    assert await gate.authorize("ha_reload_config_entry", {"entry_id": "x"}) is None

    assert [r.outcome.value for r in gate.records] == ["auto_approved", "auto_approved"]
    assert all(r.executed for r in gate.records)


@pytest.mark.asyncio
async def test_approval_mode_without_a_channel_refuses():
    gate = _gate(GateMode.APPROVAL)

    refusal = await gate.authorize("pods_delete", {"name": "x"})

    assert refusal is not None
    assert "no approval channel" in refusal
    assert not gate.records[0].executed


@pytest.mark.asyncio
async def test_approval_mode_includes_action_summary_and_plan():
    from unittest.mock import AsyncMock

    from lyoko.application.safety.hitl.manager import ApprovalManager
    from lyoko.domain.models.chat import ApprovalResponse

    approval_manager = ApprovalManager()
    chat_manager = AsyncMock()
    broadcast_mock = AsyncMock()
    chat_manager.broadcast_approval_request = broadcast_mock

    gate = _gate(
        GateMode.APPROVAL,
        approval_manager=approval_manager,
        chat_manager=chat_manager,
        plan="Scale deployment radarr to 2 replicas",
    )

    # Trigger authorization as a background task since wait_for_approval waits
    import asyncio

    task = asyncio.create_task(
        gate.authorize("resources_scale", {"name": "radarr", "namespace": "media", "replicas": 2})
    )

    await asyncio.sleep(0.01)
    assert broadcast_mock.called
    req = broadcast_mock.call_args[0][0]
    assert "Action: Scale radarr to 2 replicas in namespace 'media'." in req.details
    assert "Plan: Scale deployment radarr to 2 replicas" in req.details
    assert "Tool: `resources_scale`" in req.details

    # Resolve approval
    approval_manager.resolve_approval(
        ApprovalResponse(incident_id=req.incident_id, approved=True, user_id="123")
    )
    res = await task
    assert res is None


@pytest.mark.asyncio
async def test_approval_request_is_sent_into_the_message_thread():
    import asyncio
    from unittest.mock import AsyncMock

    from lyoko.application.safety.hitl.manager import ApprovalManager
    from lyoko.domain.models.chat import ApprovalResponse

    approval_manager = ApprovalManager()
    chat_manager = AsyncMock()

    gate = _gate(
        GateMode.APPROVAL,
        approval_manager=approval_manager,
        chat_manager=chat_manager,
        message_thread_id="321",
    )

    task = asyncio.create_task(gate.authorize("pods_delete", {"name": "x"}))
    await asyncio.sleep(0.01)

    assert chat_manager.broadcast_approval_request.call_args.kwargs["message_thread_id"] == "321"

    req = chat_manager.broadcast_approval_request.call_args.args[0]
    approval_manager.resolve_approval(
        ApprovalResponse(incident_id=req.incident_id, approved=False, user_id="123")
    )
    await task


@pytest.mark.asyncio
async def test_approval_omits_unreadable_arguments_and_names_the_resource():
    """A huge manifest must not be dumped in the prompt; the Action names the resource instead."""
    import asyncio
    from unittest.mock import AsyncMock

    from lyoko.application.safety.hitl.manager import ApprovalManager
    from lyoko.domain.models.chat import ApprovalResponse

    approval_manager = ApprovalManager()
    chat_manager = AsyncMock()
    gate = _gate(GateMode.APPROVAL, approval_manager=approval_manager, chat_manager=chat_manager)
    manifest = (
        "apiVersion: argoproj.io/v1alpha1\nkind: Application\nmetadata:\n  name: arr-stack\n"
        "  namespace: argocd\n"
    ) * 40

    task = asyncio.create_task(
        gate.authorize("k8s_resources_create_or_update", {"resource": manifest})
    )
    await asyncio.sleep(0.01)
    req = chat_manager.broadcast_approval_request.call_args.args[0]

    assert "Apply changes to Application 'arr-stack'." in req.details
    assert "Arguments:" not in req.details

    approval_manager.resolve_approval(
        ApprovalResponse(incident_id=req.incident_id, approved=True, user_id="1")
    )
    await task


def test_describe_action_falls_back_without_a_manifest():
    from lyoko.application.safety.hitl.descriptor import ApprovalActionDescriptor

    assert (
        ApprovalActionDescriptor.describe_action(
            "k8s_resources_create_or_update",
            {"kind": "ConfigMap", "name": "cfg", "namespace": "media"},
        )
        == "Apply changes to ConfigMap 'cfg' in namespace 'media'."
    )


def test_matching_is_case_sensitive_so_lookalikes_do_not_slip_through():
    assert not matches_any("PODS_GET", ["pods_get"])


def test_make_gate_reads_the_message_thread_from_state():
    from lyoko.application.safety.tool_gate import make_gate

    gate = make_gate(
        {"event_id": "chat-1", "chat_id": "42", "message_thread_id": "321"},
        GateMode.APPROVAL,
    )

    assert gate._message_thread_id == "321"


def test_parse_diagnosis_actionable():
    diagnosis = parse_diagnosis(
        "Some thinking first.\n"
        "ROOT_CAUSE: Disk is full on node-1.\n"
        "It filled up overnight.\n"
        "ACTIONABLE: Yes\n"
        "PLAN: 1. Prune images.\n2. Verify."
    )

    assert diagnosis.root_cause == "Disk is full on node-1.\nIt filled up overnight."
    assert diagnosis.actionable is True
    assert diagnosis.plan == "1. Prune images.\n2. Verify."


def test_parse_diagnosis_not_actionable_and_plan_required():
    assert parse_diagnosis("ROOT_CAUSE: x\nACTIONABLE: no\nPLAN: call a human").actionable is False
    assert parse_diagnosis("ROOT_CAUSE: x\nACTIONABLE: yes\nPLAN:").actionable is False


def test_parse_diagnosis_unparseable_text_is_not_actionable():
    diagnosis = parse_diagnosis("I could not figure it out.")

    assert diagnosis.actionable is False
    assert diagnosis.root_cause == "I could not figure it out."


def test_parse_result():
    assert parse_result("thinking\nRESULT: Scaled it.\nIt worked.") == "Scaled it.\nIt worked."
    assert parse_result("No marker here") == "No marker here"


@pytest.mark.parametrize(
    ("text", "resolved", "evidence"),
    [
        ("RESOLVED\nPod is Running.", True, "Pod is Running."),
        ("resolved: all good", True, "all good"),
        ("**RESOLVED** - healthy", True, "healthy"),
        ("UNRESOLVED\nStill crashing.", False, "Still crashing."),
        ("Not resolved yet", False, "Not resolved yet"),
        ("", False, ""),
    ],
)
def test_parse_verdict(text, resolved, evidence):
    assert parse_verdict(text) == (resolved, evidence)
