from uuid import UUID

from app.features.redirect.service import (
    evaluate_rules,
    rule_matches,
    weighted_random_choice,
)
from app.models import AccessRule


class FakeTargetUrl:
    def __init__(self, url: str, weight: int = 1, is_active: bool = True):
        self.url = url
        self.weight = weight
        self.is_active = is_active


class FakeShortLink:
    def __init__(self, default_action: str = "allow"):
        self.default_action = default_action


def _rule(**kwargs) -> AccessRule:
    defaults = {
        "name": "test rule",
        "action": "allow",
        "priority": 0,
        "countries": [],
        "ua_platforms": [],
        "referer_patterns": [],
        "client_requirement": "any",
        "proxy_requirement": "any",
        "is_active": True,
    }
    defaults.update(kwargs)
    return AccessRule(**defaults)


def test_rule_requires_human_and_non_proxy_together():
    rule = _rule(client_requirement="human", proxy_requirement="non_proxy")

    assert rule_matches(rule, None, "bot", None, True) is False
    assert rule_matches(rule, None, "pc", None, True) is False
    assert rule_matches(rule, None, "pc", None, False) is True


def test_rule_matches_bot_only_requirement():
    rule = _rule(client_requirement="bot")

    assert rule_matches(rule, None, "bot", None, False) is True
    assert rule_matches(rule, None, "pc", None, False) is False


def test_rule_matches_proxy_only_requirement():
    rule = _rule(proxy_requirement="proxy")

    assert rule_matches(rule, None, "pc", None, True) is True
    assert rule_matches(rule, None, "pc", None, False) is False


def test_rule_matches_country_platform_and_any_referer_pattern():
    rule = _rule(
        countries=["CN"],
        ua_platforms=["mobile"],
        referer_patterns=["*facebook*", "*tiktok*"],
    )

    assert rule_matches(rule, "cn", "mobile", "https://facebook.com/a", False) is True
    assert rule_matches(rule, "US", "mobile", "https://facebook.com/a", False) is False
    assert rule_matches(rule, "CN", "pc", "https://facebook.com/a", False) is False
    assert rule_matches(rule, "CN", "mobile", "https://google.com/a", False) is False


def test_evaluate_rules_ignores_inactive_rule_and_returns_default_without_match():
    inactive = _rule(action="deny", is_active=False)

    outcome = evaluate_rules(
        [inactive], FakeShortLink("deny"), None, None, None, False
    )

    assert outcome.action == "deny"
    assert outcome.matched_rule_id is None
    assert outcome.matched_rule_priority is None


def test_evaluate_rules_uses_priority_then_id_for_matching_rule():
    earlier = _rule(
        id=UUID("00000000-0000-0000-0000-000000000001"), name="first", action="deny"
    )
    later = _rule(
        id=UUID("00000000-0000-0000-0000-000000000002"), name="second", action="allow"
    )

    outcome = evaluate_rules(
        [later, earlier], FakeShortLink(), None, None, None, False
    )

    assert outcome.action == "deny"
    assert outcome.matched_rule_id == earlier.id
    assert outcome.matched_rule_priority == earlier.priority


def test_weighted_random_choice_ignores_inactive_and_non_positive_weights():
    urls = [
        FakeTargetUrl("inactive", is_active=False),
        FakeTargetUrl("zero", weight=0),
        FakeTargetUrl("active"),
    ]

    assert weighted_random_choice(urls).url == "active"
