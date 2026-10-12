"""Deterministic detection and repair of an Argo CD application stuck OutOfSync."""

from typing import Any

import yaml

_AUTOSYNC_MARKER = "Argo CD application "
_OUTOFSYNC_MARKER = "OutOfSync"
_ARGOCD_NAMESPACE = "argocd"


class ArgoCDApplicationInspector:
    """Inspector and mutator for Argo CD Application manifests."""

    @staticmethod
    def parse_outofsync_app(text: str) -> str | None:
        """Return the application name from an Argo CD OutOfSync alert, or ``None``.

        Parses "Argo CD application <name> has sync status OutOfSync" by plain string search, so no
        regular expression is involved. Returns ``None`` when the text is not that kind of alert.
        """
        if not text or _OUTOFSYNC_MARKER not in text:
            return None
        lowered = text.lower()
        index = lowered.find(_AUTOSYNC_MARKER.lower())
        if index == -1:
            return None
        rest = text[index + len(_AUTOSYNC_MARKER) :].strip()
        if not rest:
            return None
        token = rest.split()[0].strip("`'\".,:;")
        return token or None

    @staticmethod
    def manifest_from_tool_result(result: Any) -> dict[str, Any] | None:
        """Normalize a ``k8s_resources_get`` result (dict/str YAML) into a manifest dict."""
        content = result
        if isinstance(result, dict):
            content = result.get("content", result)
        if isinstance(content, dict):
            return content
        if not isinstance(content, str):
            return None
        try:
            loaded = yaml.safe_load(content)
        except yaml.YAMLError:
            return None
        return loaded if isinstance(loaded, dict) else None

    @staticmethod
    def autosync_disabled(manifest: dict[str, Any]) -> bool:
        """Return True when the Application has no autosync or it is explicitly disabled."""
        spec = manifest.get("spec") or {}
        sync_policy = spec.get("syncPolicy") or {}
        if not isinstance(sync_policy, dict):
            return True
        automated = sync_policy.get("automated")
        if automated is None:
            return True
        if isinstance(automated, dict):
            return automated.get("enabled", True) is False
        return False

    @staticmethod
    def has_sync_error(manifest: dict[str, Any]) -> bool:
        """Return True when the Application's last operation failed (a real render/sync error)."""
        operation = (manifest.get("status") or {}).get("operationState") or {}
        phase = str(operation.get("phase", "")).lower()
        if phase in {"failed", "error"}:
            return True
        conditions = (manifest.get("status") or {}).get("conditions") or []
        return any(
            isinstance(condition, dict)
            and str(condition.get("type", "")).lower() == "comparisonerror"
            and str(condition.get("status", "")).lower() == "true"
            for condition in conditions
        )

    @staticmethod
    def enable_autosync(manifest: dict[str, Any]) -> dict[str, Any]:
        """Return a copy of the manifest with ``spec.syncPolicy.automated`` enabled (prune + selfHeal)."""
        updated = dict(manifest)
        spec = dict(updated.get("spec") or {})
        sync_policy = dict(spec.get("syncPolicy") or {})
        sync_policy["automated"] = {"enabled": True, "prune": True, "selfHeal": True}
        spec["syncPolicy"] = sync_policy
        updated["spec"] = spec
        return updated

    @staticmethod
    def is_synced_and_healthy(manifest: dict[str, Any]) -> bool:
        """Return True when the Application reports Synced and Healthy."""
        status = manifest.get("status") or {}
        sync = str((status.get("sync") or {}).get("status", "")).lower()
        health = str((status.get("health") or {}).get("status", "")).lower()
        return sync == "synced" and health == "healthy"

    @staticmethod
    def manifest_to_yaml(manifest: dict[str, Any]) -> str:
        """Serialize a manifest to YAML for the server-side-apply tool."""
        return yaml.safe_dump(manifest, sort_keys=False)
