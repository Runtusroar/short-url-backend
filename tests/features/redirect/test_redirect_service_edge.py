"""Redirect service edge case tests."""

from uuid import uuid4

import pytest

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
from app.models import AccessRule, Domain, ShortLink, TargetUrl
from app.models.enums import DecisionReason, RedirectResult


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
