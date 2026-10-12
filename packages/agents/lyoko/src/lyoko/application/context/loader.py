"""Modular homelab context loader for injecting infrastructure architecture into LYOKO agents."""

from pathlib import Path

_DOCS_DIR = Path(__file__).parent / "docs"


class HomelabContextLoader:
    """Loads and aggregates homelab architectural context documents."""

    def __init__(self, context_dir: Path | None = None) -> None:
        self.context_dir = context_dir or _DOCS_DIR

    def load_context(self, filename: str) -> str:
        """Load a markdown context document from the context directory."""
        path = self.context_dir / filename
        if not path.exists():
            fallback = Path(__file__).parent / filename
            if fallback.exists():
                path = fallback
            else:
                raise FileNotFoundError(f"Homelab context file not found: {path}")
        return path.read_text(encoding="utf-8").strip()

    def get_homelab_context(
        self,
        include_overview: bool = True,
        include_ingress: bool = True,
        include_storage: bool = True,
    ) -> str:
        """Combine selected homelab architectural context sections."""
        sections: list[str] = []

        if include_overview:
            sections.append(self.load_context("homelab_overview.md"))
        if include_ingress:
            sections.append(self.load_context("ingress_gateway_api.md"))
        if include_storage:
            sections.append(self.load_context("storage_and_gitops.md"))

        if not sections:
            return ""

        body = "\n\n".join(sections)
        return (
            f"--- HOMELAB ARCHITECTURAL CONTEXT ---\n{body}\n--------------------------------------"
        )

    def build_agent_context(self, role: str) -> str:
        """Build tailored architecture context based on the agent's domain or role."""
        normalized_role = role.strip().lower()
        if normalized_role in {"k8s", "kubernetes", "diagnose", "remediate", "chat", "supervisor"}:
            return self.get_homelab_context(
                include_overview=True, include_ingress=True, include_storage=True
            )
        if normalized_role in {"network", "unifi"}:
            return self.get_homelab_context(
                include_overview=True, include_ingress=True, include_storage=False
            )
        if normalized_role in {"smarthome", "homeassistant"}:
            return self.get_homelab_context(
                include_overview=True, include_ingress=False, include_storage=False
            )
        if normalized_role in {"observability", "grafana", "prometheus"}:
            return self.get_homelab_context(
                include_overview=True, include_ingress=False, include_storage=True
            )
        if normalized_role in {"langfuse", "llm_observability"}:
            return self.get_homelab_context(
                include_overview=True, include_ingress=False, include_storage=False
            )

        return self.get_homelab_context()


_DEFAULT_LOADER = HomelabContextLoader()
load_context = _DEFAULT_LOADER.load_context
get_homelab_context = _DEFAULT_LOADER.get_homelab_context
build_agent_context = _DEFAULT_LOADER.build_agent_context
