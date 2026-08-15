"""Redirect service edge case tests."""

from uuid import uuid4
from unittest.mock import MagicMock

import pytest

from app.core.exceptions import PermissionDeniedError
from app.models import AccessRule, Domain, ShortLink
from app.features.redirect import service
from app.features.redirect.service import (
    _match_list,
    _match_referer,
    execute_redirect,
    evaluate_rules,
    rule_matches,
    weighted_random_choice,
)
from app.features.redirect.ua import get_platform


def test_ua_platform_variants():
    assert get_platform("Mozilla/5.0 (iPad; CPU OS 17_0 like Mac OS X) AppleWebKit/...") == "tablet"
    assert get_platform("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/...") == "pc"
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

    monkeypatch.setattr("app.features.redirect.ua.parse", lambda value: BotThatLooksLikePc())
    assert get_platform("spoofed-bot") == "bot"


def test_match_list_empty_candidates():
    assert _match_list("CN", []) is True
    assert _match_list(None, ["CN"]) is False


def test_match_referer_pattern():
    assert _match_referer("https://example.com/foo", "https://example.com/*") is True
    assert _match_referer(None, "https://example.com/*") is False
    assert _match_referer("https://example.com/foo", None) is True


def test_match_referer_multiple_patterns():
    assert _match_referer("https://facebook.com/foo", "*facebook*,*tiktok*") is True
    assert _match_referer("https://www.tiktok.com/foo", "*facebook*,*tiktok*") is True
    assert _match_referer("https://google.com/foo", "*facebook*,*tiktok*") is False


def test_rule_matches_all_conditions():
    rule = MagicMock()
    rule.countries = ["CN"]
    rule.ua_platforms = ["mobile"]
    rule.referer_pattern = "https://example.com/*"
    rule.allow_proxy = True
    rule.allow_bot = True

    assert rule_matches(rule, "CN", "mobile", "https://example.com/foo", False) is True
    assert rule_matches(rule, "US", "mobile", "https://example.com/foo", False) is False
    assert rule_matches(rule, "CN", "pc", "https://example.com/foo", False) is False
    assert rule_matches(rule, "CN", "mobile", "https://other.com/foo", False) is False


def test_evaluate_rules_priority():
    deny = MagicMock()
    deny.is_active = True
    deny.priority = 1
    deny.action = "deny"
    deny.countries = deny.ua_platforms = None
    deny.referer_pattern = None
    deny.allow_proxy = True
    deny.allow_bot = True

    allow = MagicMock()
    allow.is_active = True
    allow.priority = 2
    allow.action = "allow"
    allow.countries = allow.ua_platforms = None
    allow.referer_pattern = None
    allow.allow_proxy = True
    allow.allow_bot = True

    short_link = MagicMock()
    short_link.default_action = "allow"
    assert evaluate_rules([allow, deny], short_link, None, None, None, False) == "deny"


def test_evaluate_rules_inactive_ignored():
    inactive = MagicMock()
    inactive.is_active = False
    inactive.priority = 1
    inactive.action = "deny"
    inactive.countries = inactive.ua_platforms = inactive.referer_pattern = None
    inactive.allow_proxy = True
    inactive.allow_bot = True

    short_link = MagicMock()
    short_link.default_action = "allow"
    assert evaluate_rules([inactive], short_link, None, None, None, False) == "allow"


def test_weighted_random_choice_excludes_non_positive_weights():
    url = MagicMock()
    url.is_active = True
    url.weight = 0
    assert weighted_random_choice([url]) is None


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


async def test_execute_redirect_preserves_bot_proxy_policy_and_logs_before_error(monkeypatch):
    domain = Domain(id=uuid4(), name="test.local", is_active=True, is_default=True)
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
        allow_bot=True,
        allow_proxy=False,
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
    assert log.ip == "203.0.113.9"
    assert log.country == "CN"
    assert log.ua_string == "Googlebot/2.1 (+http://www.google.com/bot.html)"
    assert log.ua_platform == "bot"
    assert log.referer == "https://source.example/path"
    assert log.accessed_at.tzinfo is not None
    assert log.accessed_at_plus8 == log.accessed_at.astimezone(service.ZoneInfo("Asia/Shanghai")).date()
    assert log.dedup_bucket == int(log.accessed_at.timestamp() // 30) * 30
