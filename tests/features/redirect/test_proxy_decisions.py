"""Proxy-sensitive redirect decisions and explainable access-log facts."""

import asyncio
import json
from contextlib import asynccontextmanager
from dataclasses import FrozenInstanceError, dataclass
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import settings
from app.core.exceptions import NotFoundError, PermissionDeniedError
from app.features.redirect import service
from app.integrations.maxmind.insights import (
    InsightsErrorKind,
    InsightsLookupError,
    InsightsResult,
)
from app.models import (
    AccessLog,
    AccessRule,
    Domain,
    IpBlacklist,
    ShortLink,
    TargetUrl,
    User,
)
from app.models.enums import AccessAction

GLOBAL_IP = "8.8.8.8"


def _outcome(
    action: AccessAction,
    rule_id: str | None,
    priority: int | None,
):
    return service.RuleOutcome(
        action,
        UUID(rule_id) if rule_id is not None else None,
        priority,
    )


@pytest.mark.parametrize(
    ("non_proxy_values", "proxy_values", "required"),
    [
        (
            (
                AccessAction.ALLOW,
                "00000000-0000-0000-0000-000000000001",
                1,
            ),
            (
                AccessAction.ALLOW,
                "00000000-0000-0000-0000-000000000001",
                99,
            ),
            False,
        ),
        (
            (AccessAction.DENY, None, None),
            (AccessAction.DENY, None, None),
            False,
        ),
        (
            (AccessAction.ALLOW, None, None),
            (AccessAction.DENY, None, None),
            True,
        ),
        (
            (
                AccessAction.ALLOW,
                "00000000-0000-0000-0000-000000000001",
                1,
            ),
            (
                AccessAction.ALLOW,
                "00000000-0000-0000-0000-000000000002",
                1,
            ),
            True,
        ),
    ],
)
def test_proxy_assessment_required_compares_only_action_and_matched_rule(
    non_proxy_values,
    proxy_values,
    required,
):
    non_proxy = _outcome(*non_proxy_values)
    proxy = _outcome(*proxy_values)
    assert service.proxy_assessment_required(non_proxy, proxy) is required


@pytest.mark.parametrize(
    ("non_proxy_action", "proxy_action", "expected_branch"),
    [
        (AccessAction.ALLOW, AccessAction.DENY, "non_proxy"),
        (AccessAction.DENY, AccessAction.ALLOW, "proxy"),
    ],
)
def test_unknown_proxy_state_allows_when_either_branch_allows(
    non_proxy_action,
    proxy_action,
    expected_branch,
):
    non_proxy = _outcome(
        non_proxy_action,
        "00000000-0000-0000-0000-000000000010",
        10,
    )
    proxy = _outcome(
        proxy_action,
        "00000000-0000-0000-0000-000000000020",
        20,
    )

    chosen = service.choose_fail_open_outcome(non_proxy, proxy)

    assert chosen is (non_proxy if expected_branch == "non_proxy" else proxy)


@pytest.mark.parametrize("action", [AccessAction.ALLOW, AccessAction.DENY])
def test_unknown_same_action_uses_priority_then_uuid_and_ranks_default_last(action):
    higher_priority = _outcome(
        action,
        "00000000-0000-0000-0000-000000000099",
        1,
    )
    lower_uuid = _outcome(
        action,
        "00000000-0000-0000-0000-000000000001",
        5,
    )
    higher_uuid = _outcome(
        action,
        "00000000-0000-0000-0000-000000000002",
        5,
    )
    default = _outcome(action, None, None)

    assert service.choose_fail_open_outcome(lower_uuid, higher_priority) is higher_priority
    assert service.choose_fail_open_outcome(higher_uuid, lower_uuid) is lower_uuid
    assert service.choose_fail_open_outcome(default, higher_uuid) is higher_uuid


def test_rule_outcome_is_frozen_primitive_data():
    outcome = _outcome(
        AccessAction.ALLOW,
        "00000000-0000-0000-0000-000000000001",
        3,
    )

    assert outcome == service.RuleOutcome(
        AccessAction.ALLOW,
        UUID("00000000-0000-0000-0000-000000000001"),
        3,
    )
    with pytest.raises(FrozenInstanceError):
        outcome.action = AccessAction.DENY


class UnexpectedDependency:
    def __getattr__(self, name):
        raise AssertionError(f"dependency must not be touched: {name}")


class MemoryRedis:
    def __init__(self, values=None):
        self.values = dict(values or {})
        self.calls = []

    async def get(self, key):
        self.calls.append(("get", key))
        return self.values.get(key)

    async def set(self, key, value, **options):
        self.calls.append(("set", key, options))
        if options.get("nx") and key in self.values:
            return False
        self.values[key] = value
        return True

    async def eval(self, _script, _key_count, key, token):
        self.calls.append(("eval", key))
        if self.values.get(key) == token:
            del self.values[key]
            return 1
        return 0


@dataclass
class StubInsights:
    result: InsightsResult | None = None
    error: InsightsLookupError | None = None

    def __post_init__(self):
        self.calls = 0

    async def lookup(self, _ip_address):
        self.calls += 1
        if self.error is not None:
            raise self.error
        assert self.result is not None
        return self.result


class BlockingInsights(StubInsights):
    def __post_init__(self):
        super().__post_init__()
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def lookup(self, ip_address):
        self.entered.set()
        await self.release.wait()
        return await super().lookup(ip_address)


class CountingSession(AsyncSession):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.commit_count = 0

    async def commit(self):
        self.commit_count += 1
        await super().commit()


@asynccontextmanager
async def _nullpool_sessions(*, counting=False):
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    session_class = CountingSession if counting else AsyncSession
    sessions = async_sessionmaker(
        engine,
        class_=session_class,
        expire_on_commit=False,
    )
    try:
        yield sessions
    finally:
        await engine.dispose()


@dataclass(frozen=True)
class RedirectCase:
    host: str
    short_code: str
    client_ip: str
    link_id: UUID
    rule_ids: tuple[UUID, ...]
    target_ids: tuple[UUID, ...]


async def _seed_redirect_case(
    session,
    *,
    default_action="allow",
    rules=(),
    targets=("allowed", "denied"),
    blacklisted=False,
    client_ip=GLOBAL_IP,
):
    owner = await session.scalar(select(User).where(User.username == "admin"))
    domain = Domain(
        name=f"proxy-{uuid4().hex}.local",
        timezone="Asia/Shanghai",
    )
    session.add(domain)
    await session.flush()
    link = ShortLink(
        domain_id=domain.id,
        short_code=uuid4().hex[:8],
        name="proxy decision case",
        owner_id=owner.id,
        default_action=default_action,
    )
    session.add(link)
    await session.flush()

    rule_models = []
    for index, values in enumerate(rules):
        rule = AccessRule(
            short_link_id=link.id,
            name=values.get("name", f"rule {index}"),
            action=values["action"],
            priority=values.get("priority", index),
            countries=values.get("countries", []),
            ua_platforms=values.get("ua_platforms", []),
            referer_patterns=values.get("referer_patterns", []),
            client_requirement=values.get("client_requirement", "any"),
            proxy_requirement=values.get("proxy_requirement", "any"),
        )
        session.add(rule)
        rule_models.append(rule)

    target_models = []
    for url_type in targets:
        target = TargetUrl(
            short_link_id=link.id,
            url=f"https://{url_type}.example/{uuid4().hex}",
            url_type=url_type,
            weight=1,
        )
        session.add(target)
        target_models.append(target)

    if blacklisted:
        session.add(IpBlacklist(ip=client_ip, reason="test blacklist"))
    await session.commit()
    return RedirectCase(
        domain.name,
        link.short_code,
        client_ip,
        link.id,
        tuple(rule.id for rule in rule_models),
        tuple(target.id for target in target_models),
    )


async def _execute_case(session, case, *, ua="Mozilla/5.0", redis=None, insights=None):
    return await service.execute_redirect(
        session,
        case.host,
        case.short_code,
        case.client_ip,
        ua,
        "https://source.example/path",
        "GET",
        redis,
        insights,
    )


async def _case_log(session, case):
    return await session.scalar(
        select(AccessLog).where(AccessLog.short_link_id == case.link_id)
    )


def _assert_proxy_facts(
    log,
    *,
    status,
    is_anonymous,
    proxy_types,
    source,
    error_code,
):
    assert log.proxy_check_status == status
    assert log.is_anonymous is is_anonymous
    assert log.proxy_types == proxy_types
    assert log.proxy_source == source
    assert log.proxy_error_code == error_code


async def test_rule_equivalent_non_bot_skips_dependencies_and_logs_exact_facts(
    monkeypatch,
):
    monkeypatch.setattr(service.settings, "maxmind_insights_enabled", True)
    async with _nullpool_sessions() as sessions, sessions() as session:
        case = await _seed_redirect_case(
            session,
            rules=(
                {"action": "allow", "priority": 1},
                {
                    "action": "deny",
                    "priority": 2,
                    "proxy_requirement": "proxy",
                },
            ),
        )

        await _execute_case(
            session,
            case,
            redis=UnexpectedDependency(),
            insights=UnexpectedDependency(),
        )

        log = await _case_log(session, case)
        _assert_proxy_facts(
            log,
            status="skipped",
            is_anonymous=None,
            proxy_types=[],
            source=None,
            error_code=None,
        )


async def test_bot_is_assumed_proxy_without_paid_call_and_logs_exact_facts(monkeypatch):
    monkeypatch.setattr(service.settings, "maxmind_insights_enabled", True)
    async with _nullpool_sessions() as sessions, sessions() as session:
        case = await _seed_redirect_case(
            session,
            rules=(
                {"action": "allow", "proxy_requirement": "non_proxy"},
                {"action": "deny", "priority": 1, "proxy_requirement": "proxy"},
            ),
        )

        await _execute_case(
            session,
            case,
            ua="Googlebot/2.1 (+http://www.google.com/bot.html)",
            redis=UnexpectedDependency(),
            insights=UnexpectedDependency(),
        )

        log = await _case_log(session, case)
        _assert_proxy_facts(
            log,
            status="assumed_bot",
            is_anonymous=True,
            proxy_types=[],
            source="assumed_bot",
            error_code=None,
        )


async def test_cached_non_proxy_assessment_logs_exact_facts(monkeypatch):
    monkeypatch.setattr(service.settings, "maxmind_insights_enabled", True)
    redis = MemoryRedis(
        {
            f"geoip:insights:v1:{GLOBAL_IP}": json.dumps(
                {"v": 1, "is_anonymous": False, "proxy_types": []}
            )
        }
    )
    insights = StubInsights(result=InsightsResult(True, ("anonymous_vpn",)))
    async with _nullpool_sessions() as sessions, sessions() as session:
        case = await _seed_redirect_case(
            session,
            rules=(
                {"action": "allow", "proxy_requirement": "non_proxy"},
                {"action": "deny", "priority": 1, "proxy_requirement": "proxy"},
            ),
        )

        await _execute_case(session, case, redis=redis, insights=insights)

        assert insights.calls == 0
        log = await _case_log(session, case)
        _assert_proxy_facts(
            log,
            status="cached",
            is_anonymous=False,
            proxy_types=[],
            source="maxmind_insights",
            error_code=None,
        )


async def test_live_proxy_assessment_logs_exact_facts(monkeypatch):
    monkeypatch.setattr(service.settings, "maxmind_insights_enabled", True)
    redis = MemoryRedis()
    insights = StubInsights(
        result=InsightsResult(True, ("anonymous_vpn", "tor_exit_node"))
    )
    async with _nullpool_sessions() as sessions, sessions() as session:
        case = await _seed_redirect_case(
            session,
            rules=(
                {"action": "allow", "proxy_requirement": "non_proxy"},
                {"action": "deny", "priority": 1, "proxy_requirement": "proxy"},
            ),
        )

        await _execute_case(session, case, redis=redis, insights=insights)

        assert insights.calls == 1
        log = await _case_log(session, case)
        _assert_proxy_facts(
            log,
            status="checked",
            is_anonymous=True,
            proxy_types=["anonymous_vpn", "tor_exit_node"],
            source="maxmind_insights",
            error_code=None,
        )


async def test_redis_unavailable_is_unknown_fail_open_and_logs_exact_facts(monkeypatch):
    monkeypatch.setattr(service.settings, "maxmind_insights_enabled", True)
    insights = StubInsights(result=InsightsResult(True, ("anonymous_vpn",)))
    async with _nullpool_sessions() as sessions, sessions() as session:
        case = await _seed_redirect_case(
            session,
            rules=(
                {"action": "allow", "proxy_requirement": "non_proxy"},
                {"action": "deny", "priority": 1, "proxy_requirement": "proxy"},
            ),
        )

        target = await _execute_case(session, case, redis=None, insights=insights)

        assert target.startswith("https://allowed.example/")
        assert insights.calls == 0
        log = await _case_log(session, case)
        _assert_proxy_facts(
            log,
            status="error",
            is_anonymous=None,
            proxy_types=[],
            source=None,
            error_code="redis_unavailable",
        )


async def test_globally_disabled_sensitive_lookup_logs_disabled_not_skipped(monkeypatch):
    monkeypatch.setattr(service.settings, "maxmind_insights_enabled", False)
    async with _nullpool_sessions() as sessions, sessions() as session:
        case = await _seed_redirect_case(
            session,
            rules=(
                {"action": "allow", "proxy_requirement": "non_proxy"},
                {"action": "deny", "priority": 1, "proxy_requirement": "proxy"},
            ),
        )

        target = await _execute_case(
            session,
            case,
            redis=UnexpectedDependency(),
            insights=UnexpectedDependency(),
        )

        assert target.startswith("https://allowed.example/")
        log = await _case_log(session, case)
        _assert_proxy_facts(
            log,
            status="error",
            is_anonymous=None,
            proxy_types=[],
            source=None,
            error_code="disabled",
        )


async def test_maxmind_timeout_is_unknown_fail_open_and_logs_exact_facts(monkeypatch):
    monkeypatch.setattr(service.settings, "maxmind_insights_enabled", True)
    redis = MemoryRedis()
    insights = StubInsights(error=InsightsLookupError(InsightsErrorKind.TIMEOUT))
    async with _nullpool_sessions() as sessions, sessions() as session:
        case = await _seed_redirect_case(
            session,
            rules=(
                {"action": "allow", "proxy_requirement": "non_proxy"},
                {"action": "deny", "priority": 1, "proxy_requirement": "proxy"},
            ),
        )

        target = await _execute_case(session, case, redis=redis, insights=insights)

        assert target.startswith("https://allowed.example/")
        log = await _case_log(session, case)
        _assert_proxy_facts(
            log,
            status="error",
            is_anonymous=None,
            proxy_types=[],
            source="maxmind_insights",
            error_code="timeout",
        )


async def test_initial_blacklist_bypasses_assessment_and_logs_skipped_facts(monkeypatch):
    monkeypatch.setattr(service.settings, "maxmind_insights_enabled", True)
    async with _nullpool_sessions() as sessions, sessions() as session:
        case = await _seed_redirect_case(
            session,
            rules=(
                {"action": "allow", "proxy_requirement": "non_proxy"},
                {"action": "deny", "priority": 1, "proxy_requirement": "proxy"},
            ),
            blacklisted=True,
            client_ip="1.1.1.1",
        )

        target = await _execute_case(
            session,
            case,
            redis=UnexpectedDependency(),
            insights=UnexpectedDependency(),
        )

        assert target.startswith("https://denied.example/")
        log = await _case_log(session, case)
        assert log.result == "blocked"
        _assert_proxy_facts(
            log,
            status="skipped",
            is_anonymous=None,
            proxy_types=[],
            source=None,
            error_code=None,
        )


@pytest.mark.parametrize(
    ("default_action", "expected_exception", "expected_result"),
    [
        ("deny", PermissionDeniedError, "denied"),
        ("allow", NotFoundError, "allowed"),
    ],
)
async def test_no_target_access_log_commits_once_before_http_error(
    default_action,
    expected_exception,
    expected_result,
):
    async with _nullpool_sessions(counting=True) as sessions, sessions() as session:
        case = await _seed_redirect_case(
            session,
            default_action=default_action,
            targets=(),
        )
        session.commit_count = 0

        with pytest.raises(expected_exception):
            await _execute_case(
                session,
                case,
                redis=UnexpectedDependency(),
                insights=UnexpectedDependency(),
            )

        assert session.commit_count == 1
        log = await _case_log(session, case)
        assert log.result == expected_result
        assert log.decision_reason == "no_target"


async def test_external_lookup_holds_no_rule_or_target_lock_and_rereads_decision_data(
    monkeypatch,
):
    monkeypatch.setattr(service.settings, "maxmind_insights_enabled", True)
    redis = MemoryRedis()
    insights = BlockingInsights(
        result=InsightsResult(True, ("anonymous_vpn",))
    )
    async with _nullpool_sessions() as sessions:
        async with sessions() as seed_session:
            case = await _seed_redirect_case(
                seed_session,
                rules=(
                    {"action": "deny", "proxy_requirement": "proxy"},
                ),
                targets=("allowed",),
            )
        async with sessions() as redirect_session, sessions() as other_session:
            redirect_task = asyncio.create_task(
                _execute_case(
                    redirect_session,
                    case,
                    redis=redis,
                    insights=insights,
                )
            )
            try:
                await asyncio.wait_for(insights.entered.wait(), timeout=1)
                assert redirect_session.in_transaction() is False
                await asyncio.wait_for(
                    other_session.execute(
                        update(AccessRule)
                        .where(AccessRule.id == case.rule_ids[0])
                        .values(is_active=False)
                    ),
                    timeout=1,
                )
                await asyncio.wait_for(
                    other_session.execute(
                        update(TargetUrl)
                        .where(TargetUrl.id == case.target_ids[0])
                        .values(url="https://allowed.example/new")
                    ),
                    timeout=1,
                )
                await asyncio.wait_for(other_session.commit(), timeout=1)
                insights.release.set()
                target = await asyncio.wait_for(redirect_task, timeout=2)
            finally:
                insights.release.set()
                if not redirect_task.done():
                    redirect_task.cancel()
                    await asyncio.gather(redirect_task, return_exceptions=True)

        assert target == "https://allowed.example/new"
        async with sessions() as verify_session:
            log = await _case_log(verify_session, case)
            assert log.decision_reason == "default_action"
            assert log.matched_rule_id is None


async def test_blacklist_added_during_lookup_overrides_proxy_assessment(monkeypatch):
    monkeypatch.setattr(service.settings, "maxmind_insights_enabled", True)
    redis = MemoryRedis()
    insights = BlockingInsights(result=InsightsResult(False, ()))
    async with _nullpool_sessions() as sessions:
        async with sessions() as seed_session:
            case = await _seed_redirect_case(
                seed_session,
                rules=(
                    {"action": "allow", "proxy_requirement": "non_proxy"},
                    {"action": "deny", "priority": 1, "proxy_requirement": "proxy"},
                ),
                client_ip="9.9.9.9",
            )
        async with sessions() as redirect_session, sessions() as other_session:
            redirect_task = asyncio.create_task(
                _execute_case(
                    redirect_session,
                    case,
                    redis=redis,
                    insights=insights,
                )
            )
            try:
                await asyncio.wait_for(insights.entered.wait(), timeout=1)
                other_session.add(IpBlacklist(ip=case.client_ip, reason="added late"))
                await asyncio.wait_for(other_session.commit(), timeout=1)
                insights.release.set()
                target = await asyncio.wait_for(redirect_task, timeout=2)
            finally:
                insights.release.set()
                if not redirect_task.done():
                    redirect_task.cancel()
                    await asyncio.gather(redirect_task, return_exceptions=True)

        assert target.startswith("https://denied.example/")
        async with sessions() as verify_session:
            log = await _case_log(verify_session, case)
            assert log.result == "blocked"
            assert log.proxy_check_status == "checked"
            assert log.is_anonymous is False


async def test_initial_blacklist_removal_uses_skipped_unknown_fail_open_without_late_call(
    monkeypatch,
):
    monkeypatch.setattr(service.settings, "maxmind_insights_enabled", True)
    blacklist_states = iter((True, False))

    async def changing_blacklist(_db, _ip):
        return next(blacklist_states)

    monkeypatch.setattr(service, "is_blacklisted", changing_blacklist)
    async with _nullpool_sessions() as sessions, sessions() as session:
        case = await _seed_redirect_case(
            session,
            rules=(
                {"action": "allow", "proxy_requirement": "non_proxy"},
                {"action": "deny", "priority": 1, "proxy_requirement": "proxy"},
            ),
        )

        target = await _execute_case(
            session,
            case,
            redis=UnexpectedDependency(),
            insights=UnexpectedDependency(),
        )

        assert target.startswith("https://allowed.example/")
        log = await _case_log(session, case)
        _assert_proxy_facts(
            log,
            status="skipped",
            is_anonymous=None,
            proxy_types=[],
            source=None,
            error_code=None,
        )
