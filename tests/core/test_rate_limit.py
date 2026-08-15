import pytest
from starlette.requests import Request

from app.core.config import settings
from app.core.client_ip import get_client_ip
from app.core.rate_limit import rate_limit


def make_request(
    client_ip="203.0.113.10",
    x_real_ip=None,
    x_forwarded_for=None,
):
    headers = []
    if x_real_ip is not None:
        headers.append((b"x-real-ip", x_real_ip.encode()))
    if x_forwarded_for is not None:
        headers.append((b"x-forwarded-for", x_forwarded_for.encode()))
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/auth/login",
            "headers": headers,
            "client": (client_ip, 12345),
            "server": ("testserver", 80),
            "scheme": "http",
            "query_string": b"",
        }
    )


def default_limiter_identifier(monkeypatch):
    monkeypatch.setattr(settings, "redis_url", "redis://test")
    dependency = rate_limit(times=60, seconds=60)
    identifier = dependency.dependency.identifier
    assert identifier is not None
    return identifier


@pytest.mark.asyncio
async def test_auth_default_limiter_ignores_untrusted_proxy_headers(monkeypatch):
    monkeypatch.setattr(settings, "trust_proxy_headers", False)
    request = make_request(
        x_real_ip="198.51.100.7",
        x_forwarded_for="192.0.2.99",
    )

    actual = await default_limiter_identifier(monkeypatch)(request)

    assert actual == get_client_ip(request) == "203.0.113.10"


@pytest.mark.asyncio
async def test_auth_default_limiter_uses_trusted_real_ip(monkeypatch):
    monkeypatch.setattr(settings, "trust_proxy_headers", True)
    request = make_request(
        x_real_ip="198.51.100.7",
        x_forwarded_for="192.0.2.99",
    )

    actual = await default_limiter_identifier(monkeypatch)(request)

    assert actual == get_client_ip(request) == "198.51.100.7"


@pytest.mark.asyncio
async def test_auth_default_limiter_rejects_invalid_real_ip(monkeypatch):
    monkeypatch.setattr(settings, "trust_proxy_headers", True)
    request = make_request(
        x_real_ip="not-an-ip",
        x_forwarded_for="192.0.2.99",
    )

    actual = await default_limiter_identifier(monkeypatch)(request)

    assert actual == get_client_ip(request) == "203.0.113.10"
