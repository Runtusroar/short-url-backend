from importlib import import_module
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_core_modules_own_shared_infrastructure():
    expected = {
        "config": {"Settings", "settings"},
        "database": {"Base", "AsyncSessionLocal", "engine", "get_db"},
        "security": {
            "verify_password",
            "get_password_hash",
            "create_access_token",
            "decode_token",
            "get_current_user",
            "require_role",
            "require_admin",
            "require_staff",
        },
        "exceptions": {"APIError", "register_exception_handlers"},
        "rate_limit": {"rate_limit"},
    }
    for module_name, names in expected.items():
        module = import_module(f"app.core.{module_name}")
        assert names <= set(dir(module))


def test_flat_core_modules_are_removed():
    for name in ("auth.py", "config.py", "database.py", "dependencies.py", "exceptions.py", "rate_limit.py"):
        assert not (ROOT / "app" / name).exists()
