"""Redis-governed policy for paid proxy-intelligence lookups."""

import asyncio
import ipaddress
import json
import logging
import secrets
from collections.abc import Awaitable
from dataclasses import dataclass, field
from typing import TypeVar

from redis.asyncio import Redis

from app.integrations.maxmind.insights import (
    InsightsLookupError,
    InsightsResult,
    MaxMindInsightsClient,
)
from app.models.enums import ProxyCheckStatus, ProxyErrorCode, ProxySource

logger = logging.getLogger(__name__)

SUCCESS_TTL_SECONDS = 86400
TRANSIENT_ERROR_TTL_SECONDS = 120
LONG_BREAKER_TTL_SECONDS = 1800
RATE_LIMIT_BREAKER_TTL_SECONDS = 60
LOCK_TTL_SECONDS = 5
REDIS_OPERATION_BUDGET_SECONDS = 0.25

_T = TypeVar("_T")

PROXY_TYPES = (
    "anonymous_vpn",
    "hosting_provider",
    "public_proxy",
    "residential_proxy",
    "tor_exit_node",
)

COMPARE_DELETE_LUA = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("del", KEYS[1])
end
return 0
"""

_MAXMIND_ERROR_CODES = frozenset(
    {
        ProxyErrorCode.AUTH_FAILED,
        ProxyErrorCode.INSUFFICIENT_FUNDS,
        ProxyErrorCode.PERMISSION_DENIED,
        ProxyErrorCode.RATE_LIMITED,
        ProxyErrorCode.TIMEOUT,
        ProxyErrorCode.UPSTREAM_ERROR,
        ProxyErrorCode.INVALID_RESPONSE,
        ProxyErrorCode.IP_NOT_FOUND,
    }
)
_LONG_BREAKER_ERRORS = frozenset(
    {
        ProxyErrorCode.AUTH_FAILED,
        ProxyErrorCode.INSUFFICIENT_FUNDS,
        ProxyErrorCode.PERMISSION_DENIED,
    }
)


@dataclass(frozen=True)
class ProxyAssessment:
    is_anonymous: bool | None
    proxy_types: tuple[str, ...]
    status: ProxyCheckStatus
    source: ProxySource | None
    error_code: ProxyErrorCode | None

    @classmethod
    def unknown(
        cls,
        error_code: ProxyErrorCode,
        source: ProxySource | None = None,
    ) -> "ProxyAssessment":
        return cls(None, (), ProxyCheckStatus.ERROR, source, error_code)


@dataclass
class RedisKeyspace:
    prefix: str = ""
    created_keys: set[str] = field(default_factory=set)

    def _track(self, key: str) -> str:
        self.created_keys.add(key)
        return key

    def success(self, ip: str) -> str:
        return self._track(f"{self.prefix}geoip:insights:v1:{ip}")

    def error(self, ip: str) -> str:
        return self._track(f"{self.prefix}geoip:insights:error:v1:{ip}")

    def breaker(self) -> str:
        return self._track(f"{self.prefix}maxmind:insights:disabled:v1")

    def lock(self, ip: str) -> str:
        return self._track(f"{self.prefix}geoip:insights:lock:v1:{ip}")


def decode_cache_record(raw: object) -> ProxyAssessment | None:
    """Decode a versioned success or MaxMind error record, rejecting drift."""
    if not isinstance(raw, str):
        return None
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if (
        not isinstance(value, dict)
        or type(value.get("v")) is not int
        or value["v"] != 1
    ):
        return None

    if set(value) == {"v", "is_anonymous", "proxy_types"}:
        is_anonymous = value["is_anonymous"]
        proxy_types = value["proxy_types"]
        if not isinstance(is_anonymous, bool) or not isinstance(proxy_types, list):
            return None
        if any(not isinstance(proxy_type, str) for proxy_type in proxy_types):
            return None
        try:
            positions = tuple(
                PROXY_TYPES.index(proxy_type) for proxy_type in proxy_types
            )
        except ValueError:
            return None
        if positions != tuple(sorted(set(positions))):
            return None
        return ProxyAssessment(
            is_anonymous,
            tuple(proxy_types),
            ProxyCheckStatus.CACHED,
            ProxySource.MAXMIND_INSIGHTS,
            None,
        )

    if set(value) == {"v", "error_code"}:
        try:
            error_code = ProxyErrorCode(value["error_code"])
        except (TypeError, ValueError):
            return None
        if error_code not in _MAXMIND_ERROR_CODES:
            return None
        return ProxyAssessment.unknown(error_code, ProxySource.MAXMIND_INSIGHTS)

    return None


def _canonical_global_ip(value: str) -> tuple[str | None, ProxyErrorCode | None]:
    if not isinstance(value, str):
        return None, ProxyErrorCode.INVALID_IP
    try:
        address = ipaddress.ip_address(value)
    except (TypeError, ValueError):
        return None, ProxyErrorCode.INVALID_IP
    if not address.is_global:
        return None, ProxyErrorCode.NON_GLOBAL_IP
    return str(address), None


def _malformed_cache_assessment() -> ProxyAssessment:
    return ProxyAssessment.unknown(ProxyErrorCode.INVALID_RESPONSE)


async def _run_redis_operation(operation: Awaitable[_T]) -> _T:
    return await asyncio.wait_for(
        operation,
        timeout=REDIS_OPERATION_BUDGET_SECONDS,
    )


async def _read_cached_assessment(
    redis: Redis,
    canonical_ip: str,
    keys: RedisKeyspace,
) -> ProxyAssessment | None:
    success_raw = await _run_redis_operation(redis.get(keys.success(canonical_ip)))
    if success_raw is not None:
        decoded = decode_cache_record(success_raw)
        if decoded is None or decoded.status is not ProxyCheckStatus.CACHED:
            return _malformed_cache_assessment()
        return decoded

    error_raw = await _run_redis_operation(redis.get(keys.error(canonical_ip)))
    if error_raw is not None:
        decoded = decode_cache_record(error_raw)
        if decoded is None or decoded.status is not ProxyCheckStatus.ERROR:
            return _malformed_cache_assessment()
        return decoded

    breaker_raw = await _run_redis_operation(redis.get(keys.breaker()))
    if breaker_raw is not None:
        decoded = decode_cache_record(breaker_raw)
        if decoded is None or decoded.status is not ProxyCheckStatus.ERROR:
            return _malformed_cache_assessment()
        return decoded

    return None


def _validated_result(result: InsightsResult) -> InsightsResult | None:
    if not isinstance(result.is_anonymous, bool):
        return None
    if not isinstance(result.proxy_types, tuple):
        return None
    if any(not isinstance(proxy_type, str) for proxy_type in result.proxy_types):
        return None
    try:
        positions = tuple(PROXY_TYPES.index(value) for value in result.proxy_types)
    except ValueError:
        return None
    if positions != tuple(sorted(set(positions))):
        return None
    return result


def _success_record(result: InsightsResult) -> str:
    return json.dumps(
        {
            "v": 1,
            "is_anonymous": result.is_anonymous,
            "proxy_types": list(result.proxy_types),
        },
        separators=(",", ":"),
    )


def _error_record(error_code: ProxyErrorCode) -> str:
    return json.dumps(
        {"v": 1, "error_code": error_code.value},
        separators=(",", ":"),
    )


def _warn_post_lookup_redis_failure(exc: Exception, canonical_ip: str) -> None:
    logger.warning(
        "proxy_intelligence_redis_post_lookup_failure",
        extra={
            "error_class": type(exc).__name__,
            "ip_address": canonical_ip,
        },
    )


async def _cache_assessment(
    redis: Redis,
    keys: RedisKeyspace,
    canonical_ip: str,
    assessment: ProxyAssessment,
    result: InsightsResult | None,
) -> None:
    if result is not None:
        await _run_redis_operation(
            redis.set(
                keys.success(canonical_ip),
                _success_record(result),
                ex=SUCCESS_TTL_SECONDS,
            )
        )
        return

    error_code = assessment.error_code
    assert error_code is not None
    if error_code in _LONG_BREAKER_ERRORS:
        await _run_redis_operation(
            redis.set(
                keys.breaker(),
                _error_record(error_code),
                ex=LONG_BREAKER_TTL_SECONDS,
            )
        )
    elif error_code is ProxyErrorCode.RATE_LIMITED:
        await _run_redis_operation(
            redis.set(
                keys.breaker(),
                _error_record(error_code),
                ex=RATE_LIMIT_BREAKER_TTL_SECONDS,
            )
        )
    else:
        await _run_redis_operation(
            redis.set(
                keys.error(canonical_ip),
                _error_record(error_code),
                ex=TRANSIENT_ERROR_TTL_SECONDS,
            )
        )


async def assess_proxy(
    redis: Redis | None,
    insights: MaxMindInsightsClient | None,
    ip_address: str,
    platform: str,
    *,
    enabled: bool,
    lookup_required: bool,
    keyspace: RedisKeyspace | None = None,
) -> ProxyAssessment:
    if platform == "bot":
        return ProxyAssessment(
            True,
            (),
            ProxyCheckStatus.ASSUMED_BOT,
            ProxySource.ASSUMED_BOT,
            None,
        )
    if not lookup_required:
        return ProxyAssessment(None, (), ProxyCheckStatus.SKIPPED, None, None)
    if not enabled:
        return ProxyAssessment.unknown(ProxyErrorCode.DISABLED)

    canonical_ip, policy_error = _canonical_global_ip(ip_address)
    if policy_error is not None:
        return ProxyAssessment.unknown(policy_error)
    assert canonical_ip is not None

    if redis is None:
        return ProxyAssessment.unknown(ProxyErrorCode.REDIS_UNAVAILABLE)
    keys = keyspace if keyspace is not None else RedisKeyspace()

    try:
        cached = await _read_cached_assessment(redis, canonical_ip, keys)
    except Exception:  # noqa: BLE001 - every Redis client failure is fail-closed
        return ProxyAssessment.unknown(ProxyErrorCode.REDIS_UNAVAILABLE)
    if cached is not None:
        return cached
    if insights is None:
        return ProxyAssessment.unknown(ProxyErrorCode.DISABLED)

    token = secrets.token_urlsafe(24)
    lock_key = keys.lock(canonical_ip)
    try:
        acquired = await _run_redis_operation(
            redis.set(lock_key, token, nx=True, ex=LOCK_TTL_SECONDS)
        )
    except Exception:  # noqa: BLE001 - every Redis client failure is fail-closed
        return ProxyAssessment.unknown(ProxyErrorCode.REDIS_UNAVAILABLE)

    if not acquired:
        for _ in range(10):
            await asyncio.sleep(0.05)
            try:
                cached = await _read_cached_assessment(redis, canonical_ip, keys)
            except Exception:  # noqa: BLE001 - every Redis failure stops lookup
                return ProxyAssessment.unknown(ProxyErrorCode.REDIS_UNAVAILABLE)
            if cached is not None:
                return cached
        return ProxyAssessment.unknown(ProxyErrorCode.LOOKUP_CONTENDED)

    assessment: ProxyAssessment
    cache_result: InsightsResult | None = None
    post_lookup_redis_error: Exception | None = None
    try:
        try:
            cached = await _read_cached_assessment(redis, canonical_ip, keys)
        except Exception:  # noqa: BLE001 - every Redis failure stops lookup
            return ProxyAssessment.unknown(ProxyErrorCode.REDIS_UNAVAILABLE)
        if cached is not None:
            return cached

        try:
            result = await insights.lookup(canonical_ip)
        except InsightsLookupError as exc:
            error_code = ProxyErrorCode(exc.kind.value)
            assessment = ProxyAssessment.unknown(
                error_code,
                ProxySource.MAXMIND_INSIGHTS,
            )
        else:
            cache_result = _validated_result(result)
            if cache_result is None:
                assessment = ProxyAssessment.unknown(
                    ProxyErrorCode.INVALID_RESPONSE,
                    ProxySource.MAXMIND_INSIGHTS,
                )
            else:
                assessment = ProxyAssessment(
                    cache_result.is_anonymous,
                    cache_result.proxy_types,
                    ProxyCheckStatus.CHECKED,
                    ProxySource.MAXMIND_INSIGHTS,
                    None,
                )
        try:
            await _cache_assessment(
                redis,
                keys,
                canonical_ip,
                assessment,
                cache_result,
            )
        except Exception as exc:  # noqa: BLE001 - preserve the paid result
            post_lookup_redis_error = exc
    finally:
        try:
            await _run_redis_operation(
                redis.eval(COMPARE_DELETE_LUA, 1, lock_key, token)
            )
        except Exception as exc:  # noqa: BLE001 - preserve the paid result
            if post_lookup_redis_error is None:
                post_lookup_redis_error = exc

    if post_lookup_redis_error is not None:
        _warn_post_lookup_redis_failure(post_lookup_redis_error, canonical_ip)

    return assessment
