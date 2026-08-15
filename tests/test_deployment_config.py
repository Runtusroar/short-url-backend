import os
import subprocess
from pathlib import Path

import pytest


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


@pytest.mark.parametrize(
    ("required_url", "value"),
    [
        ("DATABASE_URL", None),
        ("DATABASE_URL", ""),
        ("REDIS_URL", None),
        ("REDIS_URL", ""),
    ],
)
def test_production_compose_rejects_missing_or_empty_service_urls(
    tmp_path, required_url, value
):
    values = {
        "APP_ENV": "prod",
        "DATABASE_URL": "postgresql+psycopg://user:pass@db:5432/app",
        "REDIS_URL": "redis://redis:6379/0",
        "SECRET_KEY": "x" * 32,
        "COOKIE_SECURE": "true",
        "CORS_ORIGINS": "https://admin.example.com",
        "LOG_LEVEL": "INFO",
        "TRUST_PROXY_HEADERS": "true",
        "GEOIPUPDATE_ACCOUNT_ID": "",
        "GEOIPUPDATE_LICENSE_KEY": "",
    }
    if value is None:
        del values[required_url]
    else:
        values[required_url] = value

    env_file = tmp_path / "production.env"
    env_file.write_text("".join(f"{key}={item}\n" for key, item in values.items()))
    process_env = os.environ.copy()
    process_env["APP_ENV"] = "prod"
    process_env.pop("DATABASE_URL", None)
    process_env.pop("REDIS_URL", None)
    result = subprocess.run(
        ["docker", "compose", "--env-file", str(env_file), "config", "--quiet"],
        cwd=ROOT,
        env=process_env,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert required_url in result.stderr


def test_make_restart_validates_then_recreates_app():
    result = subprocess.run(
        ["make", "-n", "DOCKER_COMPOSE=docker compose", "restart"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )

    commands = result.stdout.splitlines()
    assert commands == [
        "docker compose config --quiet",
        "docker compose run --rm --build --no-deps app python scripts/check_config.py",
        "docker compose up -d --force-recreate app",
    ]
    assert "docker compose restart app" not in result.stdout
