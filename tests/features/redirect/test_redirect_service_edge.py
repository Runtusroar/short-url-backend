"""Redirect service edge case tests."""

from unittest.mock import MagicMock

from app.features.redirect.service import (
    _match_list,
    _match_referer,
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


def test_weighted_random_choice_zero_total_weight():
    url = MagicMock()
    url.is_active = True
    url.weight = 0
    result = weighted_random_choice([url])
    assert result is url
