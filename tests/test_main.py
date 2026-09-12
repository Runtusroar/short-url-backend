import pytest
from types import SimpleNamespace
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError

from app.core.config import Settings
from app.core.errors import register_exception_handlers
from app.main import app, lifespan


def test_blank_maxmind_settings_are_none(monkeypatch):
    """Blank Compose substitutions must not make optional credentials invalid."""
    monkeypatch.setenv("MAXMIND_ACCOUNT_ID", "")
    monkeypatch.setenv("MAXMIND_LICENSE_KEY", "")

    configured = Settings(_env_file=None)

    assert configured.maxmind_account_id is None
    assert configured.maxmind_license_key is None


def test_blank_non_maxmind_numeric_setting_is_not_silently_converted_to_none(monkeypatch):
    """The blank-credential normalizer must not weaken unrelated numeric validation."""
    monkeypatch.setenv("IP_REPUTATION_TTL_HOURS", "")

    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_app_env_is_a_closed_deployment_mode(monkeypatch):
    """A typo in APP_ENV must not silently skip production-only safeguards."""
    monkeypatch.setenv("APP_ENV", "staging")

    with pytest.raises(ValidationError):
        Settings(_env_file=None)


@pytest.mark.parametrize(
    "secret_key",
    [
        "",
        "dev-secret-key-change-in-production",
        "change-me-to-a-random-secret-key-at-least-32-characters",
        "too-short",
    ],
)
def test_production_rejects_empty_default_or_short_secret_keys(monkeypatch, secret_key):
    """Weak signing keys in production would forge login and cursor credentials."""
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("SECRET_KEY", secret_key)
    monkeypatch.setenv("COOKIE_SECURE", "true")

    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_production_requires_secure_cookies_but_development_remains_usable(monkeypatch):
    """Production cookie transport must be explicit without breaking local startup."""
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("SECRET_KEY", "a-strong-production-secret-key-with-32-chars")
    monkeypatch.setenv("COOKIE_SECURE", "false")

    with pytest.raises(ValidationError):
        Settings(_env_file=None)

    configured = Settings(app_env="development", cookie_secure=False, _env_file=None)
    assert configured.app_env == "development"
    assert configured.cookie_secure is False


def test_public_short_url_scheme_defaults_to_http_and_production_requires_https():
    """An HTTPS deployment must never render table links with an insecure scheme."""
    development = Settings(app_env="development", _env_file=None)
    assert development.public_short_url_scheme == "http"

    with pytest.raises(ValidationError):
        Settings(
            app_env="production",
            secret_key="a-strong-production-secret-key-with-32-chars",
            cookie_secure=True,
            public_short_url_scheme="http",
            _env_file=None,
        )

    production = Settings(
        app_env="production",
        secret_key="a-strong-production-secret-key-with-32-chars",
        cookie_secure=True,
        public_short_url_scheme="https",
        _env_file=None,
    )
    assert production.public_short_url_scheme == "https"


def test_http_error_envelope_preserves_protocol_headers():
    """Discarding Retry-After or Allow turns valid HTTP error responses into broken client contracts."""
    isolated = FastAPI()
    register_exception_handlers(isolated)

    @isolated.get("/rate-limited")
    async def rate_limited():
        raise HTTPException(status_code=429, detail="Slow down", headers={"Retry-After": "30"})

    @isolated.get("/read-only")
    async def read_only():
        return {"ok": True}

    with TestClient(isolated, raise_server_exceptions=False) as client:
        limited = client.get("/rate-limited")
        method_not_allowed = client.post("/read-only")

    assert limited.status_code == 429
    assert limited.headers["retry-after"] == "30"
    assert method_not_allowed.status_code == 405
    assert method_not_allowed.headers["allow"] == "GET"


def test_unrelated_named_unique_constraint_keeps_the_generic_conflict_code():
    """Only the three named public constraints receive specialized conflict codes."""
    isolated = FastAPI()
    register_exception_handlers(isolated)

    @isolated.post("/unrelated-conflict")
    async def unrelated_conflict():
        raise IntegrityError(
            "INSERT INTO unrelated",
            {},
            SimpleNamespace(diag=SimpleNamespace(constraint_name="unrelated_unique_key")),
        )

    with TestClient(isolated, raise_server_exceptions=False) as client:
        response = client.post("/unrelated-conflict")

    assert response.status_code == 409
    assert response.json()["code"] == "CONFLICT"


def test_unhandled_exception_logs_a_traceback_but_keeps_the_stable_error_envelope(caplog):
    """Diagnostics may contain the failure, while clients must receive no implementation detail."""
    isolated = FastAPI()
    register_exception_handlers(isolated)

    @isolated.get("/boom")
    async def boom():
        raise RuntimeError("test-only failure")

    with TestClient(isolated, raise_server_exceptions=False) as client:
        response = client.get("/boom")

    assert response.status_code == 500
    assert response.json() == {"code": "INTERNAL_ERROR", "message": "服务器内部错误", "details": None}
    assert any(record.exc_info for record in caplog.records)


@pytest.mark.asyncio
async def test_lifespan_closes_the_proxy_provider(monkeypatch):
    """Skipping the shutdown close would leak the long-lived HTTP client."""
    closed = 0

    async def close_provider():
        nonlocal closed
        closed += 1

    monkeypatch.setattr("app.main.settings.redis_url", "")
    monkeypatch.setattr("app.main.close_proxy_provider", close_provider)

    async with lifespan(app):
        pass

    assert closed == 1


@pytest.mark.asyncio
async def test_lifespan_closes_proxy_even_if_redis_shutdown_fails(monkeypatch):
    """A Redis close error must not leak the independent MaxMind client."""
    closed = 0

    class FailingRedis:
        async def aclose(self):
            raise RuntimeError("redis shutdown failed")

    async def init_limiter(_redis):
        return None

    async def close_provider():
        nonlocal closed
        closed += 1

    monkeypatch.setattr("app.main.settings.redis_url", "redis://test")
    monkeypatch.setattr("app.main.redis.from_url", lambda *_args, **_kwargs: FailingRedis())
    monkeypatch.setattr("app.main.FastAPILimiter.init", init_limiter)
    monkeypatch.setattr("app.main.close_proxy_provider", close_provider)

    with pytest.raises(RuntimeError, match="redis shutdown failed"):
        async with lifespan(app):
            pass

    assert closed == 1
