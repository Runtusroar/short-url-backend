"""Persisted, fail-open IP proxy reputation lookups."""

import asyncio
import inspect
import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from ipaddress import ip_address
from typing import Any, Protocol

from geoip2.webservice import AsyncClient as MaxMindAsyncClient
from sqlalchemy import cast, select
from sqlalchemy.dialects.postgresql import INET, insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models import IpReputation
from app.db.session import AsyncSessionLocal

logger = logging.getLogger(__name__)


class ProxyProviderError(Exception):
    """A provider could not determine IP reputation."""


class InsufficientBalanceError(ProxyProviderError):
    """The provider rejected a lookup because the account has no balance."""


class ProxyClient(Protocol):
    async def lookup(self, ip: str, *, timeout: float) -> object:
        """Return one successful provider response for an IP."""


class MaxMindProxyClient:
    """Long-lived adapter around MaxMind Insights' asynchronous client."""

    def __init__(self, account_id: int, license_key: str, timeout: float):
        self._client = MaxMindAsyncClient(account_id, license_key, timeout=timeout)

    async def lookup(self, ip: str, *, timeout: float) -> object:
        return await asyncio.wait_for(self._client.insights(ip), timeout=timeout)

    async def close(self) -> None:
        await self._client.close()


_proxy_providers: dict[tuple[int, str, float], ProxyClient] = {}


def get_proxy_provider() -> ProxyClient | None:
    """Return a reused MaxMind client only when credentials are configured."""
    if settings.maxmind_account_id is None or not settings.maxmind_license_key:
        return None
    key = (
        settings.maxmind_account_id,
        settings.maxmind_license_key,
        settings.maxmind_timeout_seconds,
    )
    provider = _proxy_providers.get(key)
    if provider is None:
        provider = MaxMindProxyClient(*key)
        _proxy_providers[key] = provider
    return provider


async def close_proxy_provider() -> None:
    """Close and forget every cached provider so tests and future lifespans isolate cleanly."""
    providers = list(_proxy_providers.values())
    _proxy_providers.clear()
    for provider in providers:
        close = getattr(provider, "close", None)
        if close is not None:
            result = close()
            if inspect.isawaitable(result):
                await result


@dataclass(frozen=True, slots=True)
class ProxyResult:
    is_proxy: bool
    proxy_type: str | None
    source: str


def _clock_value(now: datetime | Callable[[], datetime]) -> datetime:
    return now() if callable(now) else now


def _normalized_ip(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return str(ip_address(value.strip()))
    except ValueError:
        return None


def _field(value: object, name: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def _normalize_provider_response(response: object) -> tuple[bool, str | None]:
    """Extract only the proxy facts policy evaluation consumes.

    A provider response must explicitly report whether the address is a proxy;
    malformed responses are unknown rather than treated as a clean address.
    """
    traits = _field(response, "traits")
    fields = traits if traits is not None else response
    is_proxy = _field(fields, "is_proxy")
    proxy_type = _field(fields, "proxy_type")
    for provider_field, normalized_type in (
        ("is_tor_exit_node", "tor_exit_node"),
        ("tor_exit_node", "tor_exit_node"),
        ("is_anonymous_vpn", "anonymous_vpn"),
        ("anonymous_vpn", "anonymous_vpn"),
        ("is_residential_proxy", "residential_proxy"),
        ("residential_proxy", "residential_proxy"),
        ("is_hosting_provider", "hosting_provider"),
        ("hosting_provider", "hosting_provider"),
        ("is_public_proxy", "public_proxy"),
        ("public_proxy", "public_proxy"),
        ("is_legitimate_proxy", "legitimate_proxy"),
        ("legitimate_proxy", "legitimate_proxy"),
        ("is_anonymous_proxy", "anonymous_proxy"),
        ("anonymous_proxy", "anonymous_proxy"),
        ("is_anonymous", "anonymous"),
        ("anonymous", "anonymous"),
    ):
        if _field(fields, provider_field) is True:
            is_proxy = True
            proxy_type = normalized_type
            break

    if is_proxy is None:
        # GeoIP2 Insights does not expose a generic ``is_proxy`` field.  Its
        # traits always include this boolean, so a false value is a successful
        # negative result worth caching.
        is_proxy = _field(fields, "is_anonymous_proxy")

    if not isinstance(is_proxy, bool):
        raise ProxyProviderError("provider response omitted proxy status")
    if proxy_type is not None:
        proxy_type = str(proxy_type)
    return is_proxy, proxy_type


async def get_proxy_result(
    db: AsyncSession | None,
    ip: str | None,
    client: ProxyClient,
    now: datetime | Callable[[], datetime],
) -> ProxyResult | None:
    """Return cached or fresh proxy reputation, or ``None`` when unknown.

    The injected client and clock make this boundary deterministic.  A provider
    failure never creates a negative reputation entry because unknown traffic
    must fail open instead of becoming a durable classification.
    """
    normalized_ip = _normalized_ip(ip)
    if normalized_ip is None:
        return None

    checked_at = _clock_value(now)
    cache_query = select(IpReputation).where(
        IpReputation.ip == cast(normalized_ip, INET),
        IpReputation.expires_at > checked_at,
    )
    try:
        if db is not None:
            cached = await db.scalar(cache_query)
        else:
            async with AsyncSessionLocal() as cache_session:
                cached = await cache_session.scalar(cache_query)
    except SQLAlchemyError as exc:
        logger.warning("proxy reputation cache read failed for %s: %s", normalized_ip, exc)
        cached = None
    if cached is not None:
        return ProxyResult(cached.is_proxy, cached.proxy_type, "cache")

    try:
        response = await client.lookup(normalized_ip, timeout=settings.maxmind_timeout_seconds)
        is_proxy, proxy_type = _normalize_provider_response(response)
    except asyncio.CancelledError:
        raise
    except (TimeoutError, asyncio.TimeoutError, InsufficientBalanceError, ProxyProviderError, OSError) as exc:
        logger.warning("proxy reputation lookup failed for %s: %s", normalized_ip, exc)
        return None
    except Exception as exc:
        # The provider is an injected external boundary; its undocumented
        # service errors must be treated as unknown and never cached.
        logger.warning("proxy reputation provider failed for %s: %s", normalized_ip, exc)
        return None

    reputation_values = {
        "ip": normalized_ip,
        "is_proxy": is_proxy,
        "proxy_type": proxy_type,
        "checked_at": checked_at,
        "expires_at": checked_at + timedelta(hours=settings.ip_reputation_ttl_hours),
    }
    cache_write = insert(IpReputation).values(**reputation_values).on_conflict_do_update(
        index_elements=[IpReputation.ip],
        set_=reputation_values,
    )
    cache_session: AsyncSession | None = None
    try:
        if db is not None:
            cache_session = db
            await cache_session.execute(cache_write)
            await cache_session.commit()
        else:
            async with AsyncSessionLocal() as cache_session:
                await cache_session.execute(cache_write)
                await cache_session.commit()
    except SQLAlchemyError as exc:
        if cache_session is not None:
            await cache_session.rollback()
        logger.warning("proxy reputation cache write failed for %s: %s", normalized_ip, exc)
    return ProxyResult(is_proxy, proxy_type, "maxmind")
