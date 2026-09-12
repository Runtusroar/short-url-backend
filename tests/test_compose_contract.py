import json
import os
import subprocess
from pathlib import Path

from app.core.config import Settings


def _compose_config(environment: dict[str, str]) -> dict:
    project_root = Path(__file__).resolve().parents[1]
    configured = subprocess.run(
        ["docker", "compose", "config", "--format", "json"],
        cwd=project_root,
        env=os.environ | environment,
        check=False,
        capture_output=True,
        text=True,
    )

    assert configured.returncode == 0, configured.stderr
    return json.loads(configured.stdout)


def test_compose_forwards_the_production_public_short_url_scheme_to_a_valid_app_config():
    """A production Compose deployment must not lose the HTTPS link scheme before app startup."""
    config = _compose_config(
        {
        "APP_ENV": "production",
        "SECRET_KEY": "a-strong-production-secret-key-with-32-chars",
        "COOKIE_SECURE": "true",
        "PUBLIC_SHORT_URL_SCHEME": "https",
        }
    )
    app_environment = config["services"]["app"]["environment"]
    assert app_environment["PUBLIC_SHORT_URL_SCHEME"] == "https"
    production = Settings(
        app_env=app_environment["APP_ENV"],
        secret_key=app_environment["SECRET_KEY"],
        cookie_secure=app_environment["COOKIE_SECURE"],
        public_short_url_scheme=app_environment["PUBLIC_SHORT_URL_SCHEME"],
        _env_file=None,
    )
    assert production.public_short_url_scheme == "https"


def test_compose_binds_all_published_backend_ports_to_loopback():
    """Published infrastructure must not bypass the host reverse proxy or firewall."""
    config = _compose_config({"SECRET_KEY": "development-secret-key"})
    expected = {
        "db": ("127.0.0.1", 18543, 5432),
        "redis": ("127.0.0.1", 16379, 6379),
        "app": ("127.0.0.1", 18000, 8000),
    }

    for service, binding in expected.items():
        ports = config["services"][service]["ports"]
        assert [
            (port.get("host_ip"), int(port["published"]), port["target"])
            for port in ports
        ] == [binding]


def test_compose_services_restart_after_a_host_reboot():
    """Every long-running production service must recover with Docker itself."""
    config = _compose_config(
        {"SECRET_KEY": "development-secret-key", "COMPOSE_PROFILES": "geoip"}
    )

    for service in ("db", "redis", "app", "geoipupdate"):
        assert config["services"][service]["restart"] == "unless-stopped"


def test_compose_uses_configured_postgres_credentials_consistently():
    """Production database credentials must not be replaced by development literals."""
    config = _compose_config(
        {
            "SECRET_KEY": "development-secret-key",
            "POSTGRES_USER": "configured_user",
            "POSTGRES_PASSWORD": "configured_password",
            "POSTGRES_DB": "configured_db",
        }
    )
    database = config["services"]["db"]

    assert database["environment"] == {
        "POSTGRES_DB": "configured_db",
        "POSTGRES_PASSWORD": "configured_password",
        "POSTGRES_USER": "configured_user",
    }
    assert "pg_isready -U $${POSTGRES_USER} -d $${POSTGRES_DB}" in database[
        "healthcheck"
    ]["test"][-1]
