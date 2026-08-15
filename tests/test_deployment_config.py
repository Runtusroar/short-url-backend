from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def env_keys():
    keys = set()
    for line in (ROOT / ".env.example").read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            keys.add(line.split("=", 1)[0])
    return keys


def test_env_example_contains_runtime_and_compose_contract():
    assert {
        "APP_ENV",
        "POSTGRES_USER",
        "POSTGRES_PASSWORD",
        "POSTGRES_DB",
        "POSTGRES_PORT",
        "REDIS_PORT",
        "APP_PORT",
        "DATABASE_URL",
        "REDIS_URL",
        "SECRET_KEY",
        "COOKIE_SECURE",
        "CORS_ORIGINS",
        "LOG_LEVEL",
        "TRUST_PROXY_HEADERS",
    } <= env_keys()


def test_compose_ports_are_loopback_only():
    compose = (ROOT / "docker-compose.yml").read_text()
    assert '"127.0.0.1:${POSTGRES_PORT:-18543}:5432"' in compose
    assert '"127.0.0.1:${REDIS_PORT:-16379}:6379"' in compose
    assert '"127.0.0.1:${APP_PORT:-18000}:8000"' in compose
    assert "postgresql+asyncpg" not in compose


def test_makefile_has_one_environment_agnostic_entrypoint():
    makefile = (ROOT / "Makefile").read_text()
    assert "env:" in makefile
    assert "check-config:" in makefile
    assert "up: check-config" in makefile
    assert "dev-up:" not in makefile
    assert "prod-up:" not in makefile
