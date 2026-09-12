import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

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
