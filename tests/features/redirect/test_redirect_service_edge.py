"""Redirect service edge case tests."""

import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import settings
from app.core.exceptions import PermissionDeniedError
from app.features.redirect import service
from app.features.redirect.service import (
    _match_list,
    _match_referer,
    evaluate_rules,
    execute_redirect,
    get_redirect_target,
    rule_matches,
    weighted_random_choice,
)
from app.features.redirect.ua import get_platform
from app.features.short_links import service as short_link_service
from app.features.short_links.service import (
    _current_date_in_timezone,
    daily_stats_for_links,
)
from app.models import AccessLog, AccessRule, Domain, ShortLink, TargetUrl, User
from app.models.enums import DecisionReason, RedirectResult


def test_current_date_in_domain_timezone_uses_the_same_utc_instant():
    instant = datetime(2026, 1, 1, 1, 30, tzinfo=UTC)

    assert _current_date_in_timezone("Asia/Shanghai", instant).isoformat() == "2026-01-01"
    assert _current_date_in_timezone("America/New_York", instant).isoformat() == "2025-12-31"


async def test_daily_stats_default_uses_current_domain_timezone(monkeypatch):
    observed_timezones = []

    def current_date(timezone):
        observed_timezones.append(timezone)
        return datetime(2026, 1, 1, tzinfo=UTC).date()

    monkeypatch.setattr(short_link_service, "_current_date_in_timezone", current_date)
    db = _RecordingSession([_QueryResult(values=[])], [])
    await daily_stats_for_links(
        db,
        type("User", (), {"role": "admin"})(),
        Domain(id=uuid4(), timezone="America/New_York"),
        None,
    )

    assert observed_timezones == ["America/New_York"]


class _DeleteSignalSession(AsyncSession):
    delete_started: asyncio.Event

    async def execute(self, statement, *args, **kwargs):
        if getattr(statement, "is_delete", False):
            self.delete_started.set()
        return await super().execute(statement, *args, **kwargs)


@asynccontextmanager
async def _redirect_lock_sessions():
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    delete_sessions = async_sessionmaker(
        engine, class_=_DeleteSignalSession, expire_on_commit=False
    )
    try:
        yield sessions, delete_sessions
    finally:
        await engine.dispose()


async def _wait_for_delete_lock(observer, pid):
    for _ in range(200):
        wait_event = await observer.scalar(
            text("SELECT wait_event_type FROM pg_stat_activity WHERE pid = :pid"),
            {"pid": pid},
        )
        if wait_event == "Lock":
            return
        await asyncio.sleep(0)
    raise AssertionError("DELETE did not wait on the redirect transaction lock")


async def _seed_redirect_lock_case(sessions, *, with_rule):
    async with sessions() as session:
        owner = await session.scalar(select(User).where(User.username == "admin"))
        domain = Domain(name=f"lock-{uuid4().hex}.local", timezone="Asia/Shanghai")
        session.add(domain)
        await session.flush()
        link = ShortLink(
            domain_id=domain.id,
            short_code=uuid4().hex[:8],
            name="lock case",
            owner_id=owner.id,
            default_action="allow",
        )
        session.add(link)
        await session.flush()
        target = TargetUrl(
            short_link_id=link.id,
            url="https://snapshot.example/immutable",
            url_type="allowed",
        )
        session.add(target)
        rule = None
        if with_rule:
            rule = AccessRule(
                short_link_id=link.id,
                name="immutable matched rule",
                action="allow",
                priority=0,
            )
            session.add(rule)
        await session.commit()
        return domain.id, link.id, target.id, rule.id if rule else None


@pytest.mark.parametrize("locked_kind", ("target", "rule"))
async def test_redirect_decision_key_share_lock_preserves_snapshots_through_delete(
    locked_kind,
):
    async with _redirect_lock_sessions() as (sessions, delete_sessions):
        domain_id, link_id, target_id, rule_id = await _seed_redirect_lock_case(
            sessions, with_rule=locked_kind == "rule"
        )
        async with sessions() as decision_session, delete_sessions() as delete_session, sessions() as observer:
            domain = await decision_session.get(Domain, domain_id)
            link = await decision_session.get(ShortLink, link_id)
            decision = await get_redirect_target(
                decision_session, link, None, None, None, False, False
            )
            assert decision.target.id == target_id
            if locked_kind == "rule":
                assert decision.matched_rule.id == rule_id

            delete_session.delete_started = asyncio.Event()
            await delete_session.execute(
                text("SELECT set_config('application_name', :name, false)"),
                {"name": f"task5-delete-{locked_kind}-{uuid4().hex}"},
            )
            delete_pid = await delete_session.scalar(text("SELECT pg_backend_pid()"))
            table = TargetUrl if locked_kind == "target" else AccessRule
            identifier = target_id if locked_kind == "target" else rule_id
            delete_task = asyncio.create_task(
                delete_session.execute(delete(table).where(table.id == identifier))
            )
            try:
                await asyncio.wait_for(delete_session.delete_started.wait(), timeout=5)
                await _wait_for_delete_lock(observer, delete_pid)
                await service._log_access(
                    decision_session, link, domain, decision, "203.0.113.7", None,
                    None, None, None, "lock.example", "GET",
                )
                await asyncio.wait_for(delete_task, timeout=5)
                await delete_session.commit()
            finally:
                if not delete_task.done():
                    delete_task.cancel()
                    await asyncio.gather(delete_task, return_exceptions=True)
                if delete_session.in_transaction():
                    await delete_session.rollback()

        async with sessions() as verify:
            log = await verify.scalar(select(AccessLog).where(AccessLog.short_link_id == link_id))
            assert log.target_url_snapshot == "https://snapshot.example/immutable"
            if locked_kind == "target":
                assert log.target_url_id is None
            else:
                assert log.matched_rule_id is None
                assert log.matched_rule_name == "immutable matched rule"


def test_ua_platform_variants():
    assert (
        get_platform("Mozilla/5.0 (iPad; CPU OS 17_0 like Mac OS X) AppleWebKit/...")
        == "tablet"
    )
    assert (
        get_platform("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/...")
        == "pc"
    )
    assert get_platform("Googlebot/2.1 (+http://www.google.com/bot.html)") == "bot"
    assert get_platform("SomeObscureDevice/1.0") == "other"
    assert get_platform("") is None
    assert get_platform(None) is None


def test_bot_detection_precedes_pc_detection(monkeypatch):
    class BotThatLooksLikePc:
        is_bot = True
        is_mobile = False
        is_tablet = False
        is_pc = True

    monkeypatch.setattr(
        "app.features.redirect.ua.parse", lambda value: BotThatLooksLikePc()
    )
    assert get_platform("spoofed-bot") == "bot"


def test_match_list_empty_candidates():
    assert _match_list("CN", []) is True
    assert _match_list(None, ["CN"]) is False


def test_match_referer_pattern():
    assert _match_referer("https://example.com/foo", ["https://example.com/*"]) is True
    assert _match_referer(None, ["https://example.com/*"]) is False
    assert _match_referer("https://example.com/foo", None) is True


def test_match_referer_multiple_patterns():
    assert (
        _match_referer("https://facebook.com/foo", ["*facebook*", "*tiktok*"]) is True
    )
    assert (
        _match_referer("https://www.tiktok.com/foo", ["*facebook*", "*tiktok*"]) is True
    )
    assert _match_referer("https://google.com/foo", ["*facebook*", "*tiktok*"]) is False


def test_rule_matches_all_conditions():
    rule = AccessRule(
        name="all conditions",
        action="allow",
        priority=0,
        countries=["CN"],
        ua_platforms=["mobile"],
        referer_patterns=["https://example.com/*"],
        client_requirement="human",
        proxy_requirement="non_proxy",
        is_active=True,
    )

    assert rule_matches(rule, "CN", "mobile", "https://example.com/foo", False) is True
    assert rule_matches(rule, "US", "mobile", "https://example.com/foo", False) is False
    assert rule_matches(rule, "CN", "pc", "https://example.com/foo", False) is False
    assert rule_matches(rule, "CN", "mobile", "https://other.com/foo", False) is False


def test_evaluate_rules_priority():
    deny = AccessRule(
        name="deny",
        action="deny",
        priority=1,
        countries=[],
        ua_platforms=[],
        referer_patterns=[],
        client_requirement="any",
        proxy_requirement="any",
        is_active=True,
    )
    allow = AccessRule(
        name="allow",
        action="allow",
        priority=2,
        countries=[],
        ua_platforms=[],
        referer_patterns=[],
        client_requirement="any",
        proxy_requirement="any",
        is_active=True,
    )
    short_link = ShortLink(default_action="allow")
    action, matched_rule = evaluate_rules(
        [allow, deny], short_link, None, None, None, False
    )
    assert action == "deny"
    assert matched_rule is deny


def test_evaluate_rules_inactive_ignored():
    inactive = AccessRule(
        name="inactive",
        action="deny",
        priority=1,
        countries=[],
        ua_platforms=[],
        referer_patterns=[],
        is_active=False,
    )
    short_link = ShortLink(default_action="allow")
    action, matched_rule = evaluate_rules(
        [inactive], short_link, None, None, None, False
    )
    assert action == "allow"
    assert matched_rule is None


def test_weighted_random_choice_excludes_non_positive_weights():
    class Target:
        is_active = True
        weight = 0

    url = Target()
    assert weighted_random_choice([url]) is None


async def test_redirect_decision_distinguishes_matched_default_blacklist_and_no_target():
    link = ShortLink(id=uuid4(), default_action="allow")
    matched_rule = AccessRule(
        id=uuid4(),
        short_link_id=link.id,
        name="matched",
        action="allow",
        priority=0,
        countries=[],
        ua_platforms=[],
        referer_patterns=[],
        client_requirement="any",
        proxy_requirement="any",
        is_active=True,
    )
    allowed_target = TargetUrl(
        short_link_id=link.id,
        url="https://allowed.example",
        url_type="allowed",
        weight=1,
        is_active=True,
    )
    matched = await get_redirect_target(
        _RecordingSession(
            [
                _QueryResult(values=[matched_rule]),
                _QueryResult(values=[allowed_target]),
            ],
            [],
        ),
        link,
        None,
        None,
        None,
        False,
        False,
    )
    assert matched.result == RedirectResult.ALLOWED
    assert matched.reason == DecisionReason.MATCHED_RULE
    assert matched.matched_rule is matched_rule
    assert matched.target is allowed_target

    denied_target = TargetUrl(
        short_link_id=link.id,
        url="https://denied.example",
        url_type="denied",
        weight=1,
        is_active=True,
    )
    default = await get_redirect_target(
        _RecordingSession(
            [_QueryResult(values=[]), _QueryResult(values=[denied_target])], []
        ),
        ShortLink(id=link.id, default_action="deny"),
        None,
        None,
        None,
        False,
        False,
    )
    assert default.result == RedirectResult.DENIED
    assert default.reason == DecisionReason.DEFAULT_ACTION
    assert default.matched_rule is None

    blacklisted = await get_redirect_target(
        _RecordingSession([_QueryResult(values=[denied_target])], []),
        link,
        None,
        None,
        None,
        True,
        False,
    )
    assert blacklisted.result == RedirectResult.BLOCKED
    assert blacklisted.reason == DecisionReason.BLACKLIST

    no_target = await get_redirect_target(
        _RecordingSession([_QueryResult(values=[]), _QueryResult(values=[])], []),
        link,
        None,
        None,
        None,
        False,
        False,
    )
    assert no_target.result == RedirectResult.ALLOWED
    assert no_target.reason == DecisionReason.NO_TARGET
    assert no_target.target is None


class _ScalarRows:
    def __init__(self, values):
        self._values = values

    def all(self):
        return self._values


class _QueryResult:
    def __init__(self, *, scalar=None, values=None):
        self._scalar = scalar
        self._values = values or []

    def scalar_one_or_none(self):
        return self._scalar

    def scalars(self):
        return _ScalarRows(self._values)


class _RecordingSession:
    def __init__(self, responses, events):
        self._responses = iter(responses)
        self.events = events
        self.added = []
        self.commits = 0

    async def execute(self, statement):
        self.events.append("query")
        return next(self._responses)

    def add(self, value):
        self.events.append("log")
        self.added.append(value)

    async def commit(self):
        self.events.append("commit")
        self.commits += 1


async def test_execute_redirect_preserves_bot_proxy_policy_and_logs_before_error(
    monkeypatch,
):
    domain = Domain(
        id=uuid4(), name="test.local", timezone="Asia/Shanghai", is_active=True, is_default=True
    )
    link = ShortLink(
        id=uuid4(),
        domain_id=domain.id,
        short_code="abc123",
        is_active=True,
        default_action="deny",
    )
    rule = AccessRule(
        short_link_id=link.id,
        action="allow",
        priority=0,
        countries=["CN"],
        ua_platforms=[],
        referer_patterns=[],
        client_requirement="any",
        proxy_requirement="non_proxy",
        is_active=True,
    )
    events = []
    db = _RecordingSession(
        [
            _QueryResult(scalar=domain),
            _QueryResult(scalar=link),
            _QueryResult(scalar=None),
            _QueryResult(values=[rule]),
            _QueryResult(values=[]),
        ],
        events,
    )
    real_get_platform = service.get_platform

    def record_country(ip):
        events.append(("country", ip))
        return "CN"

    def record_platform(ua_string):
        events.append(("platform", ua_string))
        return real_get_platform(ua_string)

    monkeypatch.setattr(service, "get_country", record_country)
    monkeypatch.setattr(service, "get_platform", record_platform)

    with pytest.raises(PermissionDeniedError) as raised:
        await execute_redirect(
            db,
            "test.local",
            "abc123",
            "203.0.113.9",
            "Googlebot/2.1 (+http://www.google.com/bot.html)",
            "https://source.example/path",
            "HEAD",
        )

    assert raised.value.status_code == 403
    assert raised.value.message == "访问被拒绝"
    assert events == [
        "query",
        "query",
        "query",
        ("country", "203.0.113.9"),
        ("platform", "Googlebot/2.1 (+http://www.google.com/bot.html)"),
        "query",
        "query",
        "log",
        "commit",
    ]
    assert db.commits == 1
    assert len(db.added) == 1
    log = db.added[0]
    assert log.short_link_id == link.id
    assert log.domain_id == domain.id
    assert log.target_url_id is None
    assert log.result == "denied"
    assert log.client_ip == "203.0.113.9"
    assert log.country == "CN"
    assert log.user_agent == "Googlebot/2.1 (+http://www.google.com/bot.html)"
    assert log.ua_platform == "bot"
    assert log.referer == "https://source.example/path"
    assert log.request_host == "test.local"
    assert log.request_method == "HEAD"
    assert log.decision_reason == "no_target"
    assert log.matched_rule_id is None
    assert log.matched_rule_name is None
    assert log.target_url_snapshot is None
    assert log.proxy_check_status == "assumed_bot"
    assert log.is_anonymous is True
    assert log.proxy_types == []
    assert log.proxy_source == "assumed_bot"
    assert log.accessed_at.tzinfo is not None
    assert log.access_date == log.accessed_at.astimezone(service.ZoneInfo("Asia/Shanghai")).date()
    assert log.dedup_bucket == int(log.accessed_at.timestamp() // 30) * 30
