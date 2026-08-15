from fastapi import FastAPI

from app.config import settings
from app.main import app, lifespan


class _FakeSession:
    def __init__(self, enter_error=None):
        self.enter_error = enter_error

    async def __aenter__(self):
        if self.enter_error:
            raise self.enter_error
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return None

    async def execute(self, statement):
        return None


async def test_liveness(client):
    response = await client.get("/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_readiness(client):
    response = await client.get("/health/ready")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert body["checks"]["database"] == "ok"
    assert body["checks"]["redis"] in {"ok", "disabled"}


async def test_legacy_health_remains_compatible(client):
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_readiness_sanitizes_database_failure(client, monkeypatch):
    secret = "postgresql://readiness-user:readiness-password@database/shorturl"
    monkeypatch.setattr(
        "app.main.AsyncSessionLocal",
        lambda: _FakeSession(RuntimeError(secret)),
    )
    app.state.redis = None

    response = await client.get("/health/ready")

    assert response.status_code == 503
    assert response.json() == {
        "status": "not_ready",
        "checks": {"database": "error", "redis": "disabled"},
    }
    assert secret not in response.text
    assert "readiness-password" not in response.text


async def test_readiness_sanitizes_redis_failure(client, monkeypatch):
    secret = "redis://readiness-user:readiness-password@redis/0"

    class FailingRedis:
        async def ping(self):
            raise RuntimeError(secret)

    monkeypatch.setattr("app.main.AsyncSessionLocal", lambda: _FakeSession())
    app.state.redis = FailingRedis()

    response = await client.get("/health/ready")

    assert response.status_code == 503
    assert response.json() == {
        "status": "not_ready",
        "checks": {"database": "ok", "redis": "error"},
    }
    assert secret not in response.text
    assert "readiness-password" not in response.text


async def test_redis_enabled_lifespan_initializes_stores_and_closes_client(monkeypatch):
    initialized_clients = []

    class FakeRedis:
        def __init__(self):
            self.close_calls = 0

        async def aclose(self):
            self.close_calls += 1

    redis_client = FakeRedis()

    def fake_from_url(url, *, encoding, decode_responses):
        assert url == "redis://test/0"
        assert encoding == "utf-8"
        assert decode_responses is True
        return redis_client

    async def fake_limiter_init(client):
        initialized_clients.append(client)

    monkeypatch.setattr(settings, "redis_url", "redis://test/0")
    monkeypatch.setattr("app.main.redis.from_url", fake_from_url)
    monkeypatch.setattr(
        "app.main.FastAPILimiter.init",
        staticmethod(fake_limiter_init),
    )
    test_app = FastAPI()

    async with lifespan(test_app):
        assert test_app.state.redis is redis_client
        assert initialized_clients == [redis_client]
        assert redis_client.close_calls == 0

    assert redis_client.close_calls == 1
    assert test_app.state.redis is None


async def test_redis_disabled_lifespan_keeps_state_none(monkeypatch):
    def unexpected_from_url(*args, **kwargs):
        raise AssertionError("Redis must not be initialized when disabled")

    monkeypatch.setattr(settings, "redis_url", "")
    monkeypatch.setattr("app.main.redis.from_url", unexpected_from_url)
    test_app = FastAPI()

    async with lifespan(test_app):
        assert test_app.state.redis is None

    assert test_app.state.redis is None
