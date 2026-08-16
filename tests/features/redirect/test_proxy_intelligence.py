import asyncio
import json
import logging
import os
import uuid
from dataclasses import dataclass

import pytest
import pytest_asyncio
from redis.asyncio import Redis

from app.features.redirect.proxy_intelligence import (
    ProxyAssessment,
    RedisKeyspace,
    assess_proxy,
    decode_cache_record,
)
from app.integrations.maxmind.insights import (
    InsightsErrorKind,
    InsightsLookupError,
    InsightsResult,
)
from app.models.enums import ProxyCheckStatus, ProxyErrorCode, ProxySource

GLOBAL_IPV4 = "8.8.8.8"
EXPANDED_GLOBAL_IPV6 = "2001:4860:4860:0:0:0:0:8888"
CANONICAL_GLOBAL_IPV6 = "2001:4860:4860::8888"
APPROVED_PROXY_TYPES = (
    "anonymous_vpn",
    "hosting_provider",
    "public_proxy",
    "residential_proxy",
    "tor_exit_node",
)


class UnexpectedDependency:
    def __getattr__(self, name):
        raise AssertionError(f"dependency must not be touched: {name}")


@dataclass
class StubInsights:
    result: InsightsResult | None = None
    error: InsightsLookupError | None = None

    def __post_init__(self):
        self.calls = 0
        self.addresses: list[str] = []

    async def lookup(self, ip_address: str) -> InsightsResult:
        self.calls += 1
        self.addresses.append(ip_address)
        if self.error is not None:
            raise self.error
        assert self.result is not None
        return self.result


@dataclass
class RedisCase:
    redis: Redis
    keyspace: RedisKeyspace


@pytest_asyncio.fixture
async def redis_case():
    redis_url = os.getenv("TEST_REDIS_URL", "redis://localhost:16379/0")
    redis = Redis.from_url(redis_url, encoding="utf-8", decode_responses=True)
    keyspace = RedisKeyspace(prefix=f"test:proxy-intelligence:{uuid.uuid4()}:")
    await redis.ping()
    try:
        yield RedisCase(redis=redis, keyspace=keyspace)
    finally:
        created_keys = tuple(keyspace.created_keys)
        if created_keys:
            await redis.delete(*created_keys)
            residue = [key for key in created_keys if await redis.exists(key)]
            assert residue == []
        await redis.aclose()


async def test_bot_is_assumed_proxy_without_dependencies():
    result = await assess_proxy(
        redis=None,
        insights=None,
        ip_address=GLOBAL_IPV4,
        platform="bot",
        enabled=True,
        lookup_required=False,
    )

    assert result == ProxyAssessment(
        True,
        (),
        ProxyCheckStatus.ASSUMED_BOT,
        ProxySource.ASSUMED_BOT,
        None,
    )


async def test_bot_assumption_precedes_disabled_and_invalid_ip_policy():
    result = await assess_proxy(
        redis=UnexpectedDependency(),
        insights=UnexpectedDependency(),
        ip_address="not-an-ip",
        platform="bot",
        enabled=False,
        lookup_required=True,
    )

    assert result == ProxyAssessment(
        True,
        (),
        ProxyCheckStatus.ASSUMED_BOT,
        ProxySource.ASSUMED_BOT,
        None,
    )


async def test_rule_equivalent_request_is_skipped_before_disabled_or_ip_policy():
    result = await assess_proxy(
        redis=UnexpectedDependency(),
        insights=UnexpectedDependency(),
        ip_address="not-an-ip",
        platform="browser",
        enabled=False,
        lookup_required=False,
    )

    assert result == ProxyAssessment(
        None,
        (),
        ProxyCheckStatus.SKIPPED,
        None,
        None,
    )


async def test_proxy_sensitive_request_reports_disabled_without_dependencies():
    result = await assess_proxy(
        redis=UnexpectedDependency(),
        insights=UnexpectedDependency(),
        ip_address=GLOBAL_IPV4,
        platform="browser",
        enabled=False,
        lookup_required=True,
    )

    assert result == ProxyAssessment.unknown(ProxyErrorCode.DISABLED)


@pytest.mark.parametrize("ip_address", ["", "not-an-ip", None, 123])
async def test_invalid_ip_is_local_policy_error_without_dependencies(ip_address):
    result = await assess_proxy(
        redis=UnexpectedDependency(),
        insights=UnexpectedDependency(),
        ip_address=ip_address,
        platform="browser",
        enabled=True,
        lookup_required=True,
    )

    assert result == ProxyAssessment.unknown(ProxyErrorCode.INVALID_IP)


@pytest.mark.parametrize(
    "ip_address",
    [
        "10.1.2.3",
        "127.0.0.1",
        "192.0.2.1",
        "169.254.1.1",
        "::1",
        "fc00::1",
        "fe80::1",
        "2001:db8::1",
    ],
)
async def test_non_global_ip_is_local_policy_error_without_dependencies(ip_address):
    result = await assess_proxy(
        redis=UnexpectedDependency(),
        insights=UnexpectedDependency(),
        ip_address=ip_address,
        platform="browser",
        enabled=True,
        lookup_required=True,
    )

    assert result == ProxyAssessment.unknown(ProxyErrorCode.NON_GLOBAL_IP)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (
            '{"v":1,"is_anonymous":true,"proxy_types":["anonymous_vpn","tor_exit_node"]}',
            ProxyAssessment(
                True,
                ("anonymous_vpn", "tor_exit_node"),
                ProxyCheckStatus.CACHED,
                ProxySource.MAXMIND_INSIGHTS,
                None,
            ),
        ),
        (
            '{"v":1,"is_anonymous":false,"proxy_types":[]}',
            ProxyAssessment(
                False,
                (),
                ProxyCheckStatus.CACHED,
                ProxySource.MAXMIND_INSIGHTS,
                None,
            ),
        ),
        (
            '{"v":1,"error_code":"timeout"}',
            ProxyAssessment.unknown(
                ProxyErrorCode.TIMEOUT,
                ProxySource.MAXMIND_INSIGHTS,
            ),
        ),
    ],
)
def test_cache_decoder_accepts_only_versioned_stable_records(raw, expected):
    assert decode_cache_record(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "not-json",
        "[]",
        '{"v":true,"is_anonymous":true,"proxy_types":[]}',
        '{"v":1.0,"is_anonymous":true,"proxy_types":[]}',
        '{"v":2,"is_anonymous":true,"proxy_types":[]}',
        '{"v":1,"is_anonymous":0,"proxy_types":[]}',
        '{"v":1,"is_anonymous":true,"proxy_types":"anonymous_vpn"}',
        '{"v":1,"is_anonymous":true,"proxy_types":["unknown"]}',
        '{"v":1,"is_anonymous":true,"proxy_types":["tor_exit_node","anonymous_vpn"]}',
        '{"v":1,"is_anonymous":true,"proxy_types":["anonymous_vpn","anonymous_vpn"]}',
        '{"v":1,"is_anonymous":true,"proxy_types":[],"extra":1}',
        '{"v":1,"error_code":"not_real"}',
        '{"v":1,"error_code":"disabled"}',
        '{"v":1,"error_code":"timeout","extra":1}',
        123,
        None,
    ],
)
def test_cache_decoder_rejects_malformed_or_unstable_records(raw):
    assert decode_cache_record(raw) is None


@pytest.mark.parametrize(
    ("ip_address", "canonical_ip"),
    [
        (GLOBAL_IPV4, GLOBAL_IPV4),
        (EXPANDED_GLOBAL_IPV6, CANONICAL_GLOBAL_IPV6),
    ],
)
async def test_global_ip_is_canonicalized_before_success_cache_lookup(
    redis_case, ip_address, canonical_ip
):
    await redis_case.redis.set(
        redis_case.keyspace.success(canonical_ip),
        '{"v":1,"is_anonymous":false,"proxy_types":[]}',
    )
    insights = StubInsights(result=InsightsResult(True, ("anonymous_vpn",)))

    result = await assess_proxy(
        redis_case.redis,
        insights,
        ip_address,
        "browser",
        enabled=True,
        lookup_required=True,
        keyspace=redis_case.keyspace,
    )

    assert result == ProxyAssessment(
        False,
        (),
        ProxyCheckStatus.CACHED,
        ProxySource.MAXMIND_INSIGHTS,
        None,
    )
    assert insights.calls == 0


@pytest.mark.parametrize(
    "raw",
    [
        "not-json",
        '{"v":0,"is_anonymous":true,"proxy_types":[]}',
        '{"v":1,"is_anonymous":null,"proxy_types":[]}',
        '{"v":1,"is_anonymous":true,"proxy_types":["unknown"]}',
    ],
)
async def test_malformed_success_cache_fails_open_without_paid_lookup(redis_case, raw):
    await redis_case.redis.set(redis_case.keyspace.success(GLOBAL_IPV4), raw)
    insights = StubInsights(result=InsightsResult(True, ("anonymous_vpn",)))

    result = await assess_proxy(
        redis_case.redis,
        insights,
        GLOBAL_IPV4,
        "browser",
        enabled=True,
        lookup_required=True,
        keyspace=redis_case.keyspace,
    )

    assert result == ProxyAssessment.unknown(ProxyErrorCode.INVALID_RESPONSE)
    assert insights.calls == 0


@pytest.mark.parametrize("is_anonymous", [True, False])
async def test_adapter_success_is_cached_for_one_day_including_false(
    redis_case, is_anonymous
):
    insights = StubInsights(
        result=InsightsResult(
            is_anonymous,
            ("anonymous_vpn", "public_proxy") if is_anonymous else (),
        )
    )

    result = await assess_proxy(
        redis_case.redis,
        insights,
        GLOBAL_IPV4,
        "browser",
        enabled=True,
        lookup_required=True,
        keyspace=redis_case.keyspace,
    )

    assert result == ProxyAssessment(
        is_anonymous,
        ("anonymous_vpn", "public_proxy") if is_anonymous else (),
        ProxyCheckStatus.CHECKED,
        ProxySource.MAXMIND_INSIGHTS,
        None,
    )
    raw = await redis_case.redis.get(redis_case.keyspace.success(GLOBAL_IPV4))
    assert json.loads(raw) == {
        "v": 1,
        "is_anonymous": is_anonymous,
        "proxy_types": ["anonymous_vpn", "public_proxy"] if is_anonymous else [],
    }
    assert await redis_case.redis.ttl(redis_case.keyspace.success(GLOBAL_IPV4)) in {
        86399,
        86400,
    }
    assert insights.calls == 1


@pytest.mark.parametrize(
    ("kind", "expected_code"),
    [
        (InsightsErrorKind.TIMEOUT, ProxyErrorCode.TIMEOUT),
        (InsightsErrorKind.UPSTREAM_ERROR, ProxyErrorCode.UPSTREAM_ERROR),
        (InsightsErrorKind.INVALID_RESPONSE, ProxyErrorCode.INVALID_RESPONSE),
        (InsightsErrorKind.IP_NOT_FOUND, ProxyErrorCode.IP_NOT_FOUND),
    ],
)
async def test_transient_adapter_error_is_cached_per_ip_for_120_seconds(
    redis_case, kind, expected_code
):
    insights = StubInsights(error=InsightsLookupError(kind))

    result = await assess_proxy(
        redis_case.redis,
        insights,
        GLOBAL_IPV4,
        "browser",
        enabled=True,
        lookup_required=True,
        keyspace=redis_case.keyspace,
    )

    assert result == ProxyAssessment.unknown(
        expected_code,
        ProxySource.MAXMIND_INSIGHTS,
    )
    error_key = redis_case.keyspace.error(GLOBAL_IPV4)
    assert json.loads(await redis_case.redis.get(error_key)) == {
        "v": 1,
        "error_code": expected_code.value,
    }
    assert await redis_case.redis.ttl(error_key) in {119, 120}
    assert insights.calls == 1


@pytest.mark.parametrize(
    ("kind", "expected_code", "ttl"),
    [
        (InsightsErrorKind.AUTH_FAILED, ProxyErrorCode.AUTH_FAILED, 1800),
        (
            InsightsErrorKind.INSUFFICIENT_FUNDS,
            ProxyErrorCode.INSUFFICIENT_FUNDS,
            1800,
        ),
        (
            InsightsErrorKind.PERMISSION_DENIED,
            ProxyErrorCode.PERMISSION_DENIED,
            1800,
        ),
        (InsightsErrorKind.RATE_LIMITED, ProxyErrorCode.RATE_LIMITED, 60),
    ],
)
async def test_global_adapter_breaker_uses_error_specific_ttl(
    redis_case, kind, expected_code, ttl
):
    insights = StubInsights(error=InsightsLookupError(kind))

    result = await assess_proxy(
        redis_case.redis,
        insights,
        GLOBAL_IPV4,
        "browser",
        enabled=True,
        lookup_required=True,
        keyspace=redis_case.keyspace,
    )

    assert result == ProxyAssessment.unknown(
        expected_code,
        ProxySource.MAXMIND_INSIGHTS,
    )
    breaker_key = redis_case.keyspace.breaker()
    assert json.loads(await redis_case.redis.get(breaker_key)) == {
        "v": 1,
        "error_code": expected_code.value,
    }
    assert await redis_case.redis.ttl(breaker_key) in {ttl - 1, ttl}
    assert insights.calls == 1


@pytest.mark.parametrize(
    ("record_key", "raw", "expected"),
    [
        (
            "error",
            '{"v":1,"error_code":"ip_not_found"}',
            ProxyAssessment.unknown(
                ProxyErrorCode.IP_NOT_FOUND,
                ProxySource.MAXMIND_INSIGHTS,
            ),
        ),
        (
            "breaker",
            '{"v":1,"error_code":"insufficient_funds"}',
            ProxyAssessment.unknown(
                ProxyErrorCode.INSUFFICIENT_FUNDS,
                ProxySource.MAXMIND_INSIGHTS,
            ),
        ),
    ],
)
async def test_cached_error_and_breaker_prevent_paid_lookup(
    redis_case, record_key, raw, expected
):
    key = (
        redis_case.keyspace.error(GLOBAL_IPV4)
        if record_key == "error"
        else redis_case.keyspace.breaker()
    )
    await redis_case.redis.set(key, raw)
    insights = StubInsights(result=InsightsResult(False, ()))

    result = await assess_proxy(
        redis_case.redis,
        insights,
        GLOBAL_IPV4,
        "browser",
        enabled=True,
        lookup_required=True,
        keyspace=redis_case.keyspace,
    )

    assert result == expected
    assert insights.calls == 0


async def test_default_keyspace_uses_exact_unprefixed_production_names():
    keys = RedisKeyspace()

    assert keys.success(GLOBAL_IPV4) == "geoip:insights:v1:8.8.8.8"
    assert keys.error(GLOBAL_IPV4) == "geoip:insights:error:v1:8.8.8.8"
    assert keys.breaker() == "maxmind:insights:disabled:v1"
    assert keys.lock(GLOBAL_IPV4) == "geoip:insights:lock:v1:8.8.8.8"


class FailingRedis:
    def __init__(self, delegate=None, *, fail_operation):
        self.delegate = delegate
        self.fail_operation = fail_operation

    async def get(self, *args, **kwargs):
        if self.fail_operation == "get":
            raise ConnectionError("redis-user:redis-password")
        return await self.delegate.get(*args, **kwargs)

    async def set(self, *args, **kwargs):
        is_lock = kwargs.get("nx") is True
        if self.fail_operation == "lock" and is_lock:
            raise ConnectionError("redis-user:redis-password")
        if self.fail_operation in {"cache", "cache_and_release"} and not is_lock:
            raise ConnectionError("redis-user:redis-password")
        return await self.delegate.set(*args, **kwargs)

    async def eval(self, *args, **kwargs):
        if self.fail_operation in {"release", "cache_and_release"}:
            raise ConnectionError("redis-user:redis-password")
        return await self.delegate.eval(*args, **kwargs)


@pytest.mark.parametrize("redis", [None, FailingRedis(fail_operation="get")])
async def test_redis_absence_or_initial_read_failure_suppresses_paid_lookup(redis):
    insights = StubInsights(result=InsightsResult(True, ("anonymous_vpn",)))

    result = await assess_proxy(
        redis,
        insights,
        GLOBAL_IPV4,
        "browser",
        enabled=True,
        lookup_required=True,
    )

    assert result == ProxyAssessment.unknown(ProxyErrorCode.REDIS_UNAVAILABLE)
    assert insights.calls == 0


async def test_lock_failure_suppresses_paid_lookup(redis_case):
    insights = StubInsights(result=InsightsResult(True, ("anonymous_vpn",)))
    failing_redis = FailingRedis(redis_case.redis, fail_operation="lock")

    result = await assess_proxy(
        failing_redis,
        insights,
        GLOBAL_IPV4,
        "browser",
        enabled=True,
        lookup_required=True,
        keyspace=redis_case.keyspace,
    )

    assert result == ProxyAssessment.unknown(ProxyErrorCode.REDIS_UNAVAILABLE)
    assert insights.calls == 0


class BlockingInsights(StubInsights):
    def __post_init__(self):
        super().__post_init__()
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def lookup(self, ip_address: str) -> InsightsResult:
        self.started.set()
        await self.release.wait()
        return await super().lookup(ip_address)


class CountingRedis:
    def __init__(self, delegate, expected_lock_attempts):
        self.delegate = delegate
        self.expected_lock_attempts = expected_lock_attempts
        self.lock_attempts = 0
        self.all_lock_attempted = asyncio.Event()
        self.lock_values: list[str] = []
        self.lock_options: list[dict] = []

    async def get(self, *args, **kwargs):
        return await self.delegate.get(*args, **kwargs)

    async def set(self, key, value, **kwargs):
        if kwargs.get("nx") is True:
            self.lock_attempts += 1
            self.lock_values.append(value)
            self.lock_options.append(kwargs)
            if self.lock_attempts == self.expected_lock_attempts:
                self.all_lock_attempted.set()
        return await self.delegate.set(key, value, **kwargs)

    async def eval(self, *args, **kwargs):
        return await self.delegate.eval(*args, **kwargs)


async def test_twenty_concurrent_callers_use_one_paid_lookup(redis_case):
    callers = 20
    counted_redis = CountingRedis(redis_case.redis, callers)
    insights = BlockingInsights(result=InsightsResult(True, ("anonymous_vpn",)))
    tasks = [
        asyncio.create_task(
            assess_proxy(
                counted_redis,
                insights,
                GLOBAL_IPV4,
                "browser",
                enabled=True,
                lookup_required=True,
                keyspace=redis_case.keyspace,
            )
        )
        for _ in range(callers)
    ]

    await asyncio.wait_for(insights.started.wait(), timeout=1)
    await asyncio.wait_for(counted_redis.all_lock_attempted.wait(), timeout=1)
    assert await redis_case.redis.ttl(redis_case.keyspace.lock(GLOBAL_IPV4)) in {4, 5}
    insights.release.set()
    results = await asyncio.wait_for(asyncio.gather(*tasks), timeout=2)

    assert insights.calls == 1
    assert counted_redis.lock_attempts == callers
    assert all(
        options == {"nx": True, "ex": 5} for options in counted_redis.lock_options
    )
    assert len(set(counted_redis.lock_values)) == callers
    assert sum(result.status is ProxyCheckStatus.CHECKED for result in results) == 1
    assert sum(result.status is ProxyCheckStatus.CACHED for result in results) == 19
    assert all(result.is_anonymous is True for result in results)


async def test_lock_loser_returns_contended_after_bounded_empty_rereads(redis_case):
    lock_key = redis_case.keyspace.lock(GLOBAL_IPV4)
    await redis_case.redis.set(lock_key, "other-owner", ex=5)
    insights = StubInsights(result=InsightsResult(True, ("anonymous_vpn",)))

    result = await asyncio.wait_for(
        assess_proxy(
            redis_case.redis,
            insights,
            GLOBAL_IPV4,
            "browser",
            enabled=True,
            lookup_required=True,
            keyspace=redis_case.keyspace,
        ),
        timeout=1,
    )

    assert result == ProxyAssessment.unknown(ProxyErrorCode.LOOKUP_CONTENDED)
    assert insights.calls == 0


class ReplacingOwnerInsights(StubInsights):
    def __init__(self, redis, lock_key):
        super().__init__(result=InsightsResult(False, ()))
        self.redis = redis
        self.lock_key = lock_key

    async def lookup(self, ip_address: str) -> InsightsResult:
        await self.redis.set(self.lock_key, "replacement-owner", ex=5)
        return await super().lookup(ip_address)


async def test_atomic_release_does_not_delete_a_replaced_lock_owner(redis_case):
    lock_key = redis_case.keyspace.lock(GLOBAL_IPV4)
    insights = ReplacingOwnerInsights(redis_case.redis, lock_key)

    result = await assess_proxy(
        redis_case.redis,
        insights,
        GLOBAL_IPV4,
        "browser",
        enabled=True,
        lookup_required=True,
        keyspace=redis_case.keyspace,
    )

    assert result.status is ProxyCheckStatus.CHECKED
    assert await redis_case.redis.get(lock_key) == "replacement-owner"


@pytest.mark.parametrize("fail_operation", ["cache", "release"])
async def test_post_success_redis_failure_preserves_result_and_logs_safely(
    redis_case, fail_operation, caplog
):
    insights = StubInsights(result=InsightsResult(True, ("anonymous_vpn",)))
    failing_redis = FailingRedis(redis_case.redis, fail_operation=fail_operation)
    caplog.set_level(logging.WARNING)

    result = await assess_proxy(
        failing_redis,
        insights,
        GLOBAL_IPV4,
        "browser",
        enabled=True,
        lookup_required=True,
        keyspace=redis_case.keyspace,
    )

    assert result == ProxyAssessment(
        True,
        ("anonymous_vpn",),
        ProxyCheckStatus.CHECKED,
        ProxySource.MAXMIND_INSIGHTS,
        None,
    )
    assert insights.calls == 1
    warnings = [
        record
        for record in caplog.records
        if record.getMessage() == "proxy_intelligence_redis_post_lookup_failure"
    ]
    assert warnings
    assert {record.error_class for record in warnings} == {"ConnectionError"}
    assert {record.ip_address for record in warnings} == {GLOBAL_IPV4}
    rendered = caplog.text
    assert "redis-user" not in rendered
    assert "redis-password" not in rendered


async def test_combined_post_success_redis_failures_emit_one_warning(
    redis_case, caplog
):
    insights = StubInsights(result=InsightsResult(True, ("anonymous_vpn",)))
    failing_redis = FailingRedis(redis_case.redis, fail_operation="cache_and_release")
    caplog.set_level(logging.WARNING)

    result = await assess_proxy(
        failing_redis,
        insights,
        GLOBAL_IPV4,
        "browser",
        enabled=True,
        lookup_required=True,
        keyspace=redis_case.keyspace,
    )

    assert result.status is ProxyCheckStatus.CHECKED
    warnings = [
        record
        for record in caplog.records
        if record.getMessage() == "proxy_intelligence_redis_post_lookup_failure"
    ]
    assert len(warnings) == 1
    assert warnings[0].error_class == "ConnectionError"
    assert warnings[0].ip_address == GLOBAL_IPV4
