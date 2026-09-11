from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import app


def test_health():
    with TestClient(app) as client:
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}


def test_blank_maxmind_settings_are_none(monkeypatch):
    """Blank Compose substitutions must not make optional credentials invalid."""
    monkeypatch.setenv("MAXMIND_ACCOUNT_ID", "")
    monkeypatch.setenv("MAXMIND_LICENSE_KEY", "")

    configured = Settings(_env_file=None)

    assert configured.maxmind_account_id is None
    assert configured.maxmind_license_key is None
