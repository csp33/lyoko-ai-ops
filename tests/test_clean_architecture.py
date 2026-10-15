"""Automated structural architecture tests enforcing Clean Architecture boundaries."""

import ast
from pathlib import Path

WORKSPACE_ROOT = Path(__file__).parent.parent


def get_imports_from_file(file_path: Path) -> list[str]:
    """Parse a python source file and return all imported module root names."""
    try:
        tree = ast.parse(file_path.read_text(encoding="utf-8"), filename=str(file_path))
    except Exception:
        return []

    imported_modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported_modules.append(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_modules.append(node.module)
    return imported_modules


def test_domain_layer_clean_architecture_isolation():
    """Ensure domain layers across all packages never import from application, infrastructure, or vendor SDKs."""
    forbidden_domain_prefixes = (
        "infrastructure",
        "application",
        "fastapi",
        "langchain",
        "langgraph",
        "telegram",
        "psycopg",
        "langfuse",
        "mcp",
        "lyoko.infrastructure",
        "lyoko.application",
        "sector5_mcp.infrastructure",
        "sector5_mcp.application",
    )

    domain_dirs = list(WORKSPACE_ROOT.glob("packages/**/domain"))
    assert len(domain_dirs) > 0, "Domain directories must exist."

    violations: list[str] = []
    for domain_dir in domain_dirs:
        for py_file in domain_dir.rglob("*.py"):
            imports = get_imports_from_file(py_file)
            for imp in imports:
                for forbidden in forbidden_domain_prefixes:
                    if imp == forbidden or imp.startswith(f"{forbidden}."):
                        violations.append(
                            f"{py_file.relative_to(WORKSPACE_ROOT)} imports forbidden '{imp}'"
                        )

    assert not violations, "Domain layer Clean Architecture violations found:\n" + "\n".join(
        violations
    )


def test_application_layer_clean_architecture_isolation():
    """Ensure application layers never import concrete infrastructure adapters or vendor SDKs."""
    forbidden_app_prefixes = (
        "lyoko.infrastructure",
        "sector5_mcp.infrastructure",
        "langchain_openai",
        "openai",
        "telegram",
        "psycopg",
        "psycopg_pool",
        "langfuse",
        "langgraph",
    )

    app_dirs = list(WORKSPACE_ROOT.glob("packages/**/application"))
    assert len(app_dirs) > 0, "Application directories must exist."

    violations: list[str] = []
    for app_dir in app_dirs:
        for py_file in app_dir.rglob("*.py"):
            imports = get_imports_from_file(py_file)
            for imp in imports:
                for forbidden in forbidden_app_prefixes:
                    if imp == forbidden or imp.startswith(f"{forbidden}."):
                        violations.append(
                            f"{py_file.relative_to(WORKSPACE_ROOT)} imports forbidden '{imp}'"
                        )

    assert not violations, "Application layer Clean Architecture violations found:\n" + "\n".join(
        violations
    )


def test_init_files_are_empty():
    """Ensure all __init__.py files in packages/ are completely empty (no re-exports/barrel files)."""
    non_empty: list[str] = []
    for init_file in WORKSPACE_ROOT.glob("packages/**/__init__.py"):
        content = init_file.read_text(encoding="utf-8").strip()
        if content:
            non_empty.append(
                f"{init_file.relative_to(WORKSPACE_ROOT)} is not empty ({len(content)} chars)"
            )

    assert not non_empty, "The following __init__.py files must remain empty:\n" + "\n".join(
        non_empty
    )


def test_application_and_domain_file_size_limits():
    """Ensure domain and application layer files adhere to SRP and do not grow into unmodularized god files (> 280 lines)."""
    MAX_LINES = 280
    oversized: list[str] = []

    target_dirs = list(WORKSPACE_ROOT.glob("packages/**/domain")) + list(
        WORKSPACE_ROOT.glob("packages/**/application")
    )
    for target_dir in target_dirs:
        for py_file in target_dir.rglob("*.py"):
            lines = py_file.read_text(encoding="utf-8").splitlines()
            if len(lines) > MAX_LINES:
                oversized.append(
                    f"{py_file.relative_to(WORKSPACE_ROOT)} has {len(lines)} lines (max allowed: {MAX_LINES})"
                )

    assert not oversized, (
        "Application and domain files must be modularized and kept under single-responsibility limits:\n"
        + "\n".join(oversized)
    )


def test_no_all_attribute_definitions():
    """Ensure __all__ is not defined anywhere in packages/ (anti-barrel & anti-wildcard rule)."""
    violations: list[str] = []
    for py_file in WORKSPACE_ROOT.glob("packages/**/*.py"):
        try:
            tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        except Exception:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id == "__all__":
                        violations.append(str(py_file.relative_to(WORKSPACE_ROOT)))
    assert not violations, (
        "Defining __all__ is strictly forbidden across all packages. Found in:\n"
        + "\n".join(violations)
    )
