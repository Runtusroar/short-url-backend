"""Integration coverage for Redis-backed FastAPI rate limits."""

from __future__ import annotations

import importlib

import pytest
from fastapi import FastAPI
from fastapi_limiter import FastAPILimiter
from fastapi_limiter.depends import RateLimiter
from httpx import ASGITransport, AsyncClient

import app.api.auth as auth_api
from app.core.config import settings
from app.dependencies import client_ip_identifier, rate_limit


class FakeRedis:
    """Behavioral stand-in for the Lua calls RateLimiter makes to Redis."""

    def __init__(self):
        self.keys: list[str] = []

    async def script_load(self, _script: str) -> str:
        return "rate-limit-script"

    async def evalsha(self, _sha: str, _keys: int, key: str, _times: str, _milliseconds: str) -> int:
        self.keys.append(key)
        return 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("trusted", "headers", "client", "expected_identifier"),
    [
        (
            True,
            {"X-Real-IP": "invalid", "X-Forwarded-For": "198.51.100.40, 10.0.0.1"},
            ("172.25.0.1", 5678),
            "198.51.100.40",
        ),
        (
            False,
            {"X-Real-IP": "203.0.113.40", "X-Forwarded-For": "198.51.100.40"},
            ("172.25.0.1", 5678),
            "172.25.0.1",
        ),
        (
            True,
            {"X-Real-IP": "invalid", "X-Forwarded-For": "also-invalid"},
            ("172.25.0.1", 5678),
            "unknown",
        ),
    ],
)
async def test_redis_backed_client_identifier_allows_get_and_head_with_trusted_ip_rules(
    monkeypatch, trusted, headers, client, expected_identifier
):
    """A synchronous identifier makes fastapi-limiter turn normal GET/HEAD into 500s."""
    monkeypatch.setattr(settings, "redis_url", "redis://rate-limit-test")
    monkeypatch.setattr(settings, "trust_proxy_headers", trusted)
    for attribute in ("redis", "prefix", "lua_sha", "identifier", "http_callback", "ws_callback"):
        monkeypatch.setattr(FastAPILimiter, attribute, getattr(FastAPILimiter, attribute))

    redis = FakeRedis()
    await FastAPILimiter.init(redis)
    app = FastAPI()
    dependency = rate_limit(times=10, seconds=60, identifier=client_ip_identifier)

    @app.get("/limited", dependencies=[dependency])
    async def limited_get():
        return {"method": "get"}

    @app.head("/limited", dependencies=[dependency], include_in_schema=False)
    async def limited_head():
        return None

    transport = ASGITransport(app=app, client=client)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        get_response = await http.get("/limited", headers=headers)
        head_response = await http.head("/limited", headers=headers)

    assert get_response.status_code == 200
    assert head_response.status_code == 200
    assert all(f":{expected_identifier}:" in key for key in redis.keys)
    assert len(redis.keys) == 2


def test_login_routes_explicitly_use_the_canonical_client_ip_identifier(monkeypatch):
    """Using fastapi-limiter's default identifier lets proxy headers bypass login limits."""
    previous_redis_url = settings.redis_url
    monkeypatch.setattr(settings, "redis_url", "redis://rate-limit-test")
    reloaded_auth = importlib.reload(auth_api)
    try:
        login_routes = [
            route
            for route in reloaded_auth.router.routes
            if route.path in {"/api/auth/login", "/api/auth/login-cookie"}
        ]

        assert len(login_routes) == 2
        assert all(isinstance(route.dependencies[0].dependency, RateLimiter) for route in login_routes)
        assert all(route.dependencies[0].dependency.identifier is client_ip_identifier for route in login_routes)
    finally:
        monkeypatch.setattr(settings, "redis_url", previous_redis_url)
        importlib.reload(auth_api)
