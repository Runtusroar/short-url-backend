import ast
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
LEGACY_FILES = {
    "auth.py",
    "config.py",
    "database.py",
    "dependencies.py",
    "domains.py",
    "exceptions.py",
    "models.py",
    "rate_limit.py",
    "schemas.py",
}
LEGACY_IMPORT_PREFIXES = ("app.routers", "app.services", "app.schemas")


def _is_legacy_import(name: str) -> bool:
    return any(name == prefix or name.startswith(f"{prefix}.") for prefix in LEGACY_IMPORT_PREFIXES)


def _resolve_from_module(node: ast.ImportFrom, package: str) -> str | None:
    if not node.level:
        return node.module

    package_parts = package.split(".") if package else []
    parent_levels = node.level - 1
    if parent_levels >= len(package_parts):
        return None

    base = ".".join(package_parts[: len(package_parts) - parent_levels])
    return ".".join(part for part in (base, node.module) if part)


def _import_candidates(node: ast.Import | ast.ImportFrom, package: str) -> list[str]:
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]

    module = _resolve_from_module(node, package)
    if module is None:
        return []

    candidates = [module]
    candidates.extend(f"{module}.{alias.name}" for alias in node.names)
    return candidates


def _package_for_path(path: Path) -> str:
    parts = path.relative_to(ROOT).with_suffix("").parts
    return ".".join(parts[:-1])


def _source_legacy_imports(source: str, package: str) -> list[str]:
    return [
        candidate
        for node in ast.walk(ast.parse(source))
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for candidate in _import_candidates(node, package)
        if _is_legacy_import(candidate)
    ]


@pytest.mark.parametrize(
    ("source", "package", "expected"),
    [
        ("import app.routers", "app", ["app.routers"]),
        ("import app.services", "app", ["app.services"]),
        (
            "from app.routers import router",
            "app",
            ["app.routers", "app.routers.router"],
        ),
        ("from app import routers", "app", ["app.routers"]),
        ("from app import services", "app", ["app.services"]),
        ("from app import schemas", "app", ["app.schemas"]),
        ("from . import routers", "app", ["app.routers"]),
        ("from .routers import router", "app", ["app.routers", "app.routers.router"]),
        ("from ... import services", "app.features.auth", ["app.services"]),
    ],
)
def test_legacy_import_audit_resolves_module_and_aliases(source, package, expected):
    assert _source_legacy_imports(source, package) == expected


def test_legacy_import_audit_allows_similarly_named_modules():
    assert _source_legacy_imports("import app.services_v2", "app") == []


def test_legacy_application_layout_is_removed():
    assert not ({path.name for path in (ROOT / "app").glob("*.py")} & LEGACY_FILES)
    assert not list((ROOT / "app" / "routers").glob("*.py"))
    assert not list((ROOT / "app" / "services").glob("*.py"))


def test_production_python_has_no_legacy_imports():
    violations = []
    for path in [
        *(ROOT / "app").rglob("*.py"),
        *(ROOT / "alembic").rglob("*.py"),
        *(ROOT / "scripts").rglob("*.py"),
    ]:
        tree = ast.parse(path.read_text(), filename=str(path))
        package = _package_for_path(path)
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                violations.extend(
                    f"{path}:{node.lineno}:{candidate}"
                    for candidate in _import_candidates(node, package)
                    if _is_legacy_import(candidate)
                )
    assert violations == []
