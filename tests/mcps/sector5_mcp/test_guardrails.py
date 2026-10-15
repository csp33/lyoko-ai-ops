"""Unit tests for guardrails, whitelisting, and execution boundaries."""

import pytest
from sector5_mcp.application.guardrail import GuardrailEngine
from sector5_mcp.domain.exceptions.guardrail import GuardrailViolationError
from sector5_mcp.domain.models.guardrail import GuardrailPolicy


def test_guardrail_tool_allowlist_wildcard():
    policy = GuardrailPolicy(allowed_tools=["ha_*", "pods_get*"])
    engine = GuardrailEngine(policy)

    assert engine.is_tool_allowed("ha_get_state") is True
    assert engine.is_tool_allowed("ha_set_state") is True
    assert engine.is_tool_allowed("pods_get") is True
    assert engine.is_tool_allowed("pods_delete") is False
    assert engine.is_tool_allowed("unifi_list_clients") is False


def test_guardrail_tool_denylist_precedence():
    policy = GuardrailPolicy(
        allowed_tools=["pods_*", "resources_*"],
        blocked_tools=["resources_delete", "pods_run"],
    )
    engine = GuardrailEngine(policy)

    assert engine.is_tool_allowed("pods_get") is True
    assert engine.is_tool_allowed("pods_delete") is True
    assert engine.is_tool_allowed("resources_delete") is False
    assert engine.is_tool_allowed("pods_run") is False


PROTECTED = ["kube-system", "kube-public", "longhorn-system"]


def _k8s_engine() -> GuardrailEngine:
    return GuardrailEngine(GuardrailPolicy(allowed_tools=["*"], blocked_namespaces=PROTECTED))


def _call(engine: GuardrailEngine, tool: str, arguments: dict) -> None:
    engine.validate_tool_call(tool, arguments, upstream_name="kubernetes")


def test_guardrail_protected_namespaces_allow_reads():
    engine = _k8s_engine()

    _call(engine, "pods_get", {"namespace": "kube-system", "name": "coredns"})
    _call(engine, "pods_log", {"namespace": "kube-system", "name": "coredns"})
    _call(engine, "pods_list_in_namespace", {"namespace": "kube-system"})
    _call(engine, "resources_get", {"kind": "Pod", "namespace": "kube-system", "name": "x"})
    _call(engine, "resources_list", {"kind": "Pod", "namespace": "longhorn-system"})
    _call(engine, "events_list", {"namespace": "kube-system"})
    _call(engine, "k8s_kubectl", {"command": "get pods -n kube-system"})
    _call(engine, "k8s_kubectl", {"command": "describe pod coredns -n kube-system"})


@pytest.mark.parametrize(
    ("tool", "arguments"),
    [
        ("pods_delete", {"namespace": "kube-system", "name": "coredns"}),
        ("pods_exec", {"namespace": "kube-system", "name": "coredns", "command": ["ls"]}),
        ("pods_run", {"namespace": "kube-system", "image": "busybox"}),
        ("resources_delete", {"kind": "Pod", "namespace": "longhorn-system", "name": "x"}),
        ("resources_scale", {"kind": "Deployment", "namespace": "kube-system", "name": "x"}),
        ("ns_mutating_tool_nobody_has_heard_of", {"ns": "kube-system"}),
        ("k8s_kubectl", {"command": "delete pod coredns -n kube-system"}),
        ("k8s_kubectl", {"command": "patch deployment coredns -n kube-system -p '{}'"}),
    ],
)
def test_guardrail_protected_namespaces_block_mutations(tool, arguments):
    engine = _k8s_engine()

    with pytest.raises(GuardrailViolationError) as exc:
        _call(engine, tool, arguments)
    assert "are prohibited" in str(exc.value)


def test_guardrail_protected_namespaces_inspect_resource_manifests():
    engine = _k8s_engine()
    manifest = """
apiVersion: apps/v1
kind: Deployment
metadata:
  name: coredns
  namespace: kube-system
"""
    with pytest.raises(GuardrailViolationError) as exc:
        _call(engine, "resources_create_or_update", {"resource": manifest})
    assert "kube-system" in str(exc.value)

    as_json = '{"apiVersion": "v1", "kind": "ConfigMap", "metadata": {"namespace": "kube-public"}}'
    with pytest.raises(GuardrailViolationError):
        _call(engine, "resources_create_or_update", {"resource": as_json})

    multi_doc = "kind: ConfigMap\nmetadata:\n  namespace: media\n---\nkind: ConfigMap\nmetadata:\n  namespace: kube-system\n"
    with pytest.raises(GuardrailViolationError):
        _call(engine, "resources_create_or_update", {"resource": multi_doc})

    item_list = {
        "kind": "List",
        "items": [{"kind": "Pod", "metadata": {"namespace": "kube-system"}}],
    }
    with pytest.raises(GuardrailViolationError):
        _call(engine, "resources_create_or_update", {"resource": item_list})


def test_guardrail_protected_namespaces_block_namespace_objects():
    engine = _k8s_engine()

    with pytest.raises(GuardrailViolationError):
        _call(engine, "resources_delete", {"kind": "Namespace", "name": "kube-system"})
    with pytest.raises(GuardrailViolationError):
        _call(
            engine,
            "resources_create_or_update",
            {"resource": "kind: Namespace\nmetadata:\n  name: longhorn-system\n"},
        )


def test_guardrail_blocks_uninspectable_manifest():
    engine = _k8s_engine()

    with pytest.raises(GuardrailViolationError) as exc:
        _call(engine, "resources_create_or_update", {"resource": "{unclosed: [yaml"})
    assert "cannot be inspected" in str(exc.value)


def test_guardrail_allows_mutations_outside_protected_namespaces():
    engine = _k8s_engine()

    _call(engine, "pods_delete", {"namespace": "media", "name": "radarr-xxx"})
    _call(engine, "resources_scale", {"kind": "Deployment", "namespace": "media", "name": "x"})
    _call(
        engine,
        "resources_create_or_update",
        {"resource": "kind: Deployment\nmetadata:\n  name: x\n  namespace: media\n"},
    )


def test_guardrail_namespace_check_only_applies_to_kubernetes_upstream():
    engine = _k8s_engine()

    engine.validate_tool_call(
        "ha_call_service", {"namespace": "kube-system"}, upstream_name="homeassistant"
    )


def test_guardrail_blocked_command_patterns():
    engine = GuardrailEngine(GuardrailPolicy())

    dangerous_commands = [
        "rm -rf /",
        "rm /tmp/test.txt",
        "rmdir /var/cache",
        "dd if=/dev/zero of=/dev/sda",
        "mkfs.ext4 /dev/sdb1",
        "chmod -R 777 /",
        "chown root:root /bin/bash",
        "reboot",
        "shutdown -h now",
        "echo 1 > /dev/sda",
    ]

    for cmd in dangerous_commands:
        with pytest.raises(GuardrailViolationError) as exc:
            engine.validate_tool_call(
                "pods_exec", {"namespace": "default", "command": cmd}, upstream_name="kubernetes"
            )
        assert "violates blocked execution pattern" in str(exc.value)


def test_guardrail_allowed_command_whitelist():
    policy = GuardrailPolicy(
        allowed_exec_commands=["cat", "ls", "ps", "top", "df", "ip"],
    )
    engine = GuardrailEngine(policy)

    # Allowed commands
    engine.validate_tool_call(
        "pods_exec", {"namespace": "default", "command": "cat /etc/os-release"}
    )
    engine.validate_tool_call("pods_exec", {"namespace": "default", "command": "ls -lah /var/log"})
    engine.validate_tool_call("pods_exec", {"namespace": "default", "command": "ps aux"})
    engine.validate_tool_call(
        "pods_exec", {"namespace": "default", "command": "sh -c 'cat /proc/meminfo'"}
    )

    # Disallowed binary (not in allowed list, e.g. curl)
    with pytest.raises(GuardrailViolationError) as exc:
        engine.validate_tool_call(
            "pods_exec", {"namespace": "default", "command": "curl http://external.site"}
        )
    assert "not in the allowed commands whitelist" in str(exc.value)


def test_guardrail_github_repos_allowlist_and_denylist():
    policy = GuardrailPolicy(
        allowed_github_repos=["csp33/*", "homelab-org/gitops"],
        blocked_github_repos=["csp33/secrets-vault", "*/private-*"],
    )
    engine = GuardrailEngine(policy)

    # 1. Allowed repo matching wildcard
    engine.validate_tool_call(
        "github_get_file_contents",
        {"repo": "csp33/lyoko-ai-ops", "path": "values.yaml"},
        upstream_name="github",
    )

    # 2. Allowed repo exact match
    engine.validate_tool_call(
        "github_list_tree",
        {"repository": "homelab-org/gitops"},
        upstream_name="github",
    )

    # 3. Blocked repo matching denylist
    with pytest.raises(GuardrailViolationError) as exc:
        engine.validate_tool_call(
            "github_get_file_contents",
            {"repo": "csp33/secrets-vault", "path": "passwords.txt"},
            upstream_name="github",
        )
    assert "blocked by security policy" in str(exc.value)

    # 4. Blocked repo matching pattern in denylist
    with pytest.raises(GuardrailViolationError) as exc:
        engine.validate_tool_call(
            "github_get_recent_commits",
            {"repo": "csp33/private-infra"},
            upstream_name="github",
        )
    assert "blocked by security policy" in str(exc.value)

    # 5. Repo not in allowlist
    with pytest.raises(GuardrailViolationError) as exc:
        engine.validate_tool_call(
            "github_get_file_contents",
            {"repo": "unauthorized-user/repo", "path": "main.py"},
            upstream_name="github",
        )
    assert "not permitted by allowed repositories policy" in str(exc.value)
