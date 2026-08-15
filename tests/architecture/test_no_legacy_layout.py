import ast
from pathlib import Path


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
        for node in ast.walk(tree):
            module = node.module if isinstance(node, ast.ImportFrom) else None
            names = [alias.name for alias in node.names] if isinstance(node, ast.Import) else []
            if module and module.startswith(LEGACY_IMPORT_PREFIXES):
                violations.append(f"{path}:{node.lineno}:{module}")
            violations.extend(
                f"{path}:{node.lineno}:{name}"
                for name in names
                if name.startswith(LEGACY_IMPORT_PREFIXES)
            )
    assert violations == []
