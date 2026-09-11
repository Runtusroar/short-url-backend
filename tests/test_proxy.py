from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from geoip2.records import Traits
import pytest
from sqlalchemy import cast, delete, select
from sqlalchemy.dialects.postgresql import INET

from app.db import AsyncSessionLocal
from app.db.models import IpReputation
from app.services.proxy import InsufficientBalanceError, ProxyProviderError, get_proxy_result


class Provider:
    def __init__(self, response=None, error: Exception | None = None):
        self.response = response
        self.error = error
        self.calls: list[tuple[str, float]] = []

    async def lookup(self, ip: str, *, timeout: float):
        self.calls.append((ip, timeout))
        if self.error is not None:
            raise self.error
        return self.response


@pytest.mark.parametrize(
    ("is_proxy", "proxy_type"),
    [(True, "anonymous_vpn"), (False, None)],
)
async def test_returns_cached_positive_and_negative_reputation(db, is_proxy, proxy_type):
    """Removing the cache branch would incorrectly call a paid provider."""
    now = datetime(2026, 9, 12, tzinfo=timezone.utc)
    ip = "203.0.113.10" if is_proxy else "203.0.113.11"
    db.add(
        IpReputation(
            ip=ip,
            is_proxy=is_proxy,
            proxy_type=proxy_type,
            checked_at=now - timedelta(hours=1),
            expires_at=now + timedelta(hours=1),
        )
    )
    await db.flush()
    provider = Provider(response={"is_proxy": not is_proxy, "proxy_type": "wrong"})

    result = await get_proxy_result(db, ip, provider, now)

    assert result is not None
    assert (result.is_proxy, result.proxy_type, result.source) == (is_proxy, proxy_type, "cache")
    assert provider.calls == []


async def test_expired_reputation_refreshes_from_provider_and_is_persisted():
    """Changing expiry handling to reuse stale results must fail this test."""
    now = datetime(2026, 9, 12, tzinfo=timezone.utc)
    ip = "203.0.113.12"
    async with AsyncSessionLocal() as db:
        await db.execute(delete(IpReputation).where(IpReputation.ip == cast(ip, INET)))
        db.add(
            IpReputation(
                ip=ip,
                is_proxy=False,
                proxy_type=None,
                checked_at=now - timedelta(days=8),
                expires_at=now - timedelta(seconds=1),
            )
        )
        await db.commit()
        provider = Provider(response={"is_proxy": True, "proxy_type": "anonymous_vpn"})

        result = await get_proxy_result(db, ip, provider, now)

        assert result is not None
        assert (result.is_proxy, result.proxy_type, result.source) == (True, "anonymous_vpn", "maxmind")
        assert provider.calls == [(ip, 1.5)]
        row = await db.scalar(select(IpReputation).where(IpReputation.ip == cast(ip, INET)))
        assert row is not None
        assert row.is_proxy is True
        assert row.proxy_type == "anonymous_vpn"
        assert row.checked_at == now
        assert row.expires_at == now + timedelta(hours=168)
        await db.execute(delete(IpReputation).where(IpReputation.ip == cast(ip, INET)))
        await db.commit()


@pytest.mark.parametrize(
    "error",
    [TimeoutError(), InsufficientBalanceError("balance exhausted"), ProxyProviderError("provider unavailable")],
)
async def test_provider_failures_fail_open_without_persisting_reputation(db, error):
    """Persisting an unknown provider failure as non-proxy would be unsafe."""
    now = datetime(2026, 9, 12, tzinfo=timezone.utc)
    ip = "203.0.113.13"

    result = await get_proxy_result(db, ip, Provider(error=error), now)

    assert result is None
    assert await db.scalar(select(IpReputation).where(IpReputation.ip == cast(ip, INET))) is None


@pytest.mark.parametrize(
    ("trait", "proxy_type"),
    [
        ("is_anonymous", "anonymous"),
        ("is_anonymous_proxy", "anonymous_proxy"),
        ("is_anonymous_vpn", "anonymous_vpn"),
        ("is_hosting_provider", "hosting_provider"),
        ("is_legitimate_proxy", "legitimate_proxy"),
        ("is_public_proxy", "public_proxy"),
        ("is_residential_proxy", "residential_proxy"),
        ("is_tor_exit_node", "tor_exit_node"),
    ],
)
async def test_persists_real_insights_proxy_traits_and_non_proxy_results(trait, proxy_type):
    """Dropping any GeoIP2 trait must not silently classify that proxy as clean."""
    now = datetime(2026, 9, 12, tzinfo=timezone.utc)
    cases = [(trait, True, proxy_type), (None, False, None)]
    async with AsyncSessionLocal() as db:
        for index, (enabled_trait, expected_proxy, expected_type) in enumerate(cases):
            ip = f"203.0.113.{200 + index}"
            await db.execute(delete(IpReputation).where(IpReputation.ip == cast(ip, INET)))
            await db.commit()
            traits = Traits(**({enabled_trait: True} if enabled_trait else {}))

            result = await get_proxy_result(db, ip, Provider(response=SimpleNamespace(traits=traits)), now)

            assert result is not None
            assert (result.is_proxy, result.proxy_type, result.source) == (
                expected_proxy,
                expected_type,
                "maxmind",
            )
            row = await db.scalar(select(IpReputation).where(IpReputation.ip == cast(ip, INET)))
            assert row is not None
            assert (row.is_proxy, row.proxy_type) == (expected_proxy, expected_type)
            await db.execute(delete(IpReputation).where(IpReputation.ip == cast(ip, INET)))
            await db.commit()


async def test_generic_anonymous_trait_without_a_narrower_trait_is_persisted():
    """A partial Insights response must not treat generic anonymity as clean."""
    now = datetime(2026, 9, 12, tzinfo=timezone.utc)
    ip = "203.0.113.210"
    response = SimpleNamespace(traits=SimpleNamespace(is_anonymous=True))
    async with AsyncSessionLocal() as db:
        await db.execute(delete(IpReputation).where(IpReputation.ip == cast(ip, INET)))
        await db.commit()

        result = await get_proxy_result(db, ip, Provider(response=response), now)

        assert result is not None
        assert (result.is_proxy, result.proxy_type) == (True, "anonymous")
        row = await db.scalar(select(IpReputation).where(IpReputation.ip == cast(ip, INET)))
        assert row is not None
        assert (row.is_proxy, row.proxy_type) == (True, "anonymous")
        await db.execute(delete(IpReputation).where(IpReputation.ip == cast(ip, INET)))
        await db.commit()
