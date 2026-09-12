import json
import os
import subprocess
from pathlib import Path

from app.core.config import Settings


def test_compose_forwards_the_production_public_short_url_scheme_to_a_valid_app_config():
    """A production Compose deployment must not lose the HTTPS link scheme before app startup."""
    project_root = Path(__file__).resolve().parents[1]
    environment = os.environ | {
        "APP_ENV": "production",
        "SECRET_KEY": "a-strong-production-secret-key-with-32-chars",
        "COOKIE_SECURE": "true",
        "PUBLIC_SHORT_URL_SCHEME": "https",
    }
    configured = subprocess.run(
        ["docker", "compose", "config", "--format", "json"],
        cwd=project_root,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )

    assert configured.returncode == 0, configured.stderr
    app_environment = json.loads(configured.stdout)["services"]["app"]["environment"]
    assert app_environment["PUBLIC_SHORT_URL_SCHEME"] == "https"
    production = Settings(
        app_env=app_environment["APP_ENV"],
        secret_key=app_environment["SECRET_KEY"],
        cookie_secure=app_environment["COOKIE_SECURE"],
        public_short_url_scheme=app_environment["PUBLIC_SHORT_URL_SCHEME"],
        _env_file=None,
    )
    assert production.public_short_url_scheme == "https"
