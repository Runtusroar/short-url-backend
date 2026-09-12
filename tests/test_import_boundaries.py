"""Import-boundary checks for the final backend package layout."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_MODULES = tuple(
    "app." + module
    for module in (
        "auth",
        "config",
        "database",
        "domains",
        "exceptions",
        "models",
        "rate_limit",
        "routers",
        "services.ua",
    )
)


def test_live_python_code_does_not_import_removed_compatibility_modules():
    violations: list[str] = []
    for relative_root in ("app", "tests", "scripts", "alembic"):
        for path in (ROOT / relative_root).rglob("*.py"):
            for line_number, line in enumerate(path.read_text().splitlines(), start=1):
                if any(
                    line.startswith(f"from {module}") or line.startswith(f"import {module}")
                    for module in FORBIDDEN_MODULES
                ):
                    violations.append(f"{path.relative_to(ROOT)}:{line_number}: {line}")

    assert not violations, "\n".join(violations)
