"""Contract tests for the MaxMind Insights adapter; the network is always fake."""

import asyncio
from unittest.mock import AsyncMock

import aiohttp
import pytest
from fastapi import FastAPI
from geoip2.errors import (
    AddressNotFoundError,
    AuthenticationError,
    HTTPError,
    OutOfQueriesError,
    PermissionRequiredError,
)
from geoip2.models import Insights

from app.core.config import settings
from app.main import lifespan
from app.integrations.maxmind.insights import (
    InsightsErrorKind,
    InsightsLookupError,
    InsightsResult,
    MaxMindInsightsClient,
)


ANONYMIZER_RESPONSE = {
    "anonymizer": {
        "is_anonymous": True,
        "is_anonymous_vpn": True,
        "is_hosting_provider": False,
        "is_public_proxy": False,
        "is_residential_proxy": True,
        "is_tor_exit_node": False,
    }
}


def minimal_insights_payload():
    return {
        "continent": {},
        "country": {},
        "maxmind": {},
        "registered_country": {},
        "represented_country": {},
        "traits": {
            "is_anonymous_proxy": False,
            "is_satellite_provider": False,
        },
    }


class FakeSdk:
    def __init__(self):
        self.insights = AsyncMock()
        self.close = AsyncMock()


def make_client(monkeypatch):
    fake_sdk = FakeSdk()
    constructor_calls = []

    def fake_constructor(*, account_id, license_key, timeout):
        constructor_calls.append((account_id, license_key, timeout))
        return fake_sdk

    monkeypatch.setattr(
        "app.integrations.maxmind.insights.geoip2.webservice.AsyncClient",
        fake_constructor,
    )
    client = MaxMindInsightsClient(account_id=123, license_key="secret", timeout=1.5)
    assert constructor_calls == [(123, "secret", 1.5)]
    return client, fake_sdk


async def test_lookup_reads_only_current_anonymizer(monkeypatch):
    client, fake_sdk = make_client(monkeypatch)
    record = Insights({**minimal_insights_payload(), **ANONYMIZER_RESPONSE})
    fake_sdk.insights.return_value = record

    result = await client.lookup("8.8.8.8")

    assert result == InsightsResult(
        is_anonymous=True,
        proxy_types=("anonymous_vpn", "residential_proxy"),
    )


async def test_lookup_uses_stable_proxy_type_order(monkeypatch):
    client, fake_sdk = make_client(monkeypatch)
    record = Insights(
        {
            **minimal_insights_payload(),
            "anonymizer": {
                "is_anonymous": True,
                "is_anonymous_vpn": True,
                "is_hosting_provider": True,
                "is_public_proxy": True,
                "is_residential_proxy": True,
                "is_tor_exit_node": True,
            },
        }
    )
    fake_sdk.insights.return_value = record

    result = await client.lookup("8.8.8.8")

    assert result.proxy_types == (
        "anonymous_vpn",
        "hosting_provider",
        "public_proxy",
        "residential_proxy",
        "tor_exit_node",
    )


@pytest.mark.parametrize(
    ("failure", "kind"),
    [
        (AuthenticationError("credential text"), InsightsErrorKind.AUTH_FAILED),
        (OutOfQueriesError("balance text"), InsightsErrorKind.INSUFFICIENT_FUNDS),
        (PermissionRequiredError("permission text"), InsightsErrorKind.PERMISSION_DENIED),
        (AddressNotFoundError("address text"), InsightsErrorKind.IP_NOT_FOUND),
        (asyncio.TimeoutError(), InsightsErrorKind.TIMEOUT),
        (aiohttp.ClientError("transport text"), InsightsErrorKind.UPSTREAM_ERROR),
        (HTTPError("rate body", 429, "https://secret.example", "response body"), InsightsErrorKind.RATE_LIMITED),
        (HTTPError("server body", 500, "https://secret.example", "response body"), InsightsErrorKind.UPSTREAM_ERROR),
        (HTTPError("other body", 400, "https://secret.example", "response body"), InsightsErrorKind.UPSTREAM_ERROR),
    ],
)
async def test_lookup_sanitizes_sdk_failures(monkeypatch, failure, kind):
    client, fake_sdk = make_client(monkeypatch)
    fake_sdk.insights.side_effect = failure

    with pytest.raises(InsightsLookupError) as raised:
        await client.lookup("8.8.8.8")

    assert raised.value.kind is kind
    assert str(raised.value) == kind.value
    assert "text" not in str(raised.value)
    assert "response body" not in str(raised.value)


@pytest.mark.parametrize("anonymizer", [None, {}, {"is_anonymous": "yes"}])
async def test_lookup_rejects_missing_or_malformed_anonymizer(monkeypatch, anonymizer):
    client, fake_sdk = make_client(monkeypatch)
    payload = minimal_insights_payload()
    if anonymizer is not None:
        payload["anonymizer"] = anonymizer
    fake_sdk.insights.return_value = Insights(payload)

    with pytest.raises(InsightsLookupError) as raised:
        await client.lookup("8.8.8.8")

    assert raised.value.kind is InsightsErrorKind.INVALID_RESPONSE
    assert str(raised.value) == "invalid_response"


async def test_close_delegates_once_to_sdk(monkeypatch):
    client, fake_sdk = make_client(monkeypatch)

    await client.close()

    fake_sdk.close.assert_awaited_once_with()


async def test_lifespan_owns_and_closes_one_enabled_insights_client(monkeypatch):
    created = []

    class FakeInsightsClient:
        def __init__(self, *, account_id, license_key, timeout):
            assert (account_id, license_key, timeout) == (123, "secret", 1.5)
            self.close = AsyncMock()
            created.append(self)

    async def fake_limiter_init(client):
        return None

    monkeypatch.setattr(settings, "redis_url", "")
    monkeypatch.setattr(settings, "maxmind_insights_enabled", True)
    monkeypatch.setattr(settings, "maxmind_account_id", 123)
    monkeypatch.setattr(settings, "maxmind_license_key", "secret")
    monkeypatch.setattr(settings, "maxmind_timeout_seconds", 1.5)
    monkeypatch.setattr("app.main.MaxMindInsightsClient", FakeInsightsClient)
    monkeypatch.setattr("app.main.FastAPILimiter.init", staticmethod(fake_limiter_init))
    test_app = FastAPI()

    async with lifespan(test_app):
        assert test_app.state.maxmind_insights is created[0]

    assert len(created) == 1
    created[0].close.assert_awaited_once_with()
    assert test_app.state.maxmind_insights is None


async def test_lifespan_does_not_create_disabled_insights_client(monkeypatch):
    def unexpected_client(*args, **kwargs):
        raise AssertionError("Insights must not be initialized when disabled")

    monkeypatch.setattr(settings, "redis_url", "")
    monkeypatch.setattr(settings, "maxmind_insights_enabled", False)
    monkeypatch.setattr("app.main.MaxMindInsightsClient", unexpected_client)
    test_app = FastAPI()

    async with lifespan(test_app):
        assert test_app.state.maxmind_insights is None

    assert test_app.state.maxmind_insights is None
