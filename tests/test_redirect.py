from app.models import AccessRule
from app.services.redirect import evaluate_rules, rule_matches, weighted_random_choice


class FakeTargetUrl:
    def __init__(self, url, weight=1, is_active=True):
        self.url = url
        self.weight = weight
        self.is_active = is_active


class FakeShortLink:
    def __init__(self, default_action="allow"):
        self.default_action = default_action


def _rule(**kwargs):
    defaults = {"is_active": True}
    defaults.update(kwargs)
    return AccessRule(**defaults)


def test_rule_matches_country():
    rule = _rule(countries=["CN"], ua_platforms=[], referer_pattern=None)
    assert rule_matches(rule, "CN", "pc", "https://example.com", False) is True
    assert rule_matches(rule, "US", "pc", "https://example.com", False) is False


def test_rule_matches_platform():
    rule = _rule(countries=[], ua_platforms=["mobile"], referer_pattern=None)
    assert rule_matches(rule, "CN", "mobile", None, False) is True
    assert rule_matches(rule, "CN", "pc", None, False) is False


def test_rule_matches_bot_allowed():
    rule = _rule(countries=[], ua_platforms=[], allow_bot=True)
    assert rule_matches(rule, "CN", "bot", None, False) is True


def test_rule_matches_bot_denied():
    rule = _rule(countries=[], ua_platforms=[], allow_bot=False)
    assert rule_matches(rule, "CN", "bot", None, False) is False


def test_bot_assumed_proxy_requires_proxy_permission():
    rule = _rule(countries=[], ua_platforms=[], allow_bot=True, allow_proxy=False)
    assert not rule_matches(rule, None, "bot", None, is_proxy=True)


def test_rule_matches_proxy_allowed():
    rule = _rule(countries=[], ua_platforms=[], allow_proxy=True)
    assert rule_matches(rule, "CN", "pc", None, True) is True


def test_rule_matches_proxy_denied():
    rule = _rule(countries=[], ua_platforms=[], allow_proxy=False)
    assert rule_matches(rule, "CN", "pc", None, True) is False


def test_evaluate_rules_priority():
    rule1 = _rule(action="deny", priority=0, countries=["CN"])
    rule2 = _rule(action="allow", priority=1, countries=["CN"])
    assert evaluate_rules([rule1, rule2], FakeShortLink(), "CN", "pc", None, False) == "deny"


def test_evaluate_rules_default_allow():
    assert evaluate_rules([], FakeShortLink("allow"), "CN", "pc", None, False) == "allow"


def test_evaluate_rules_default_deny():
    assert evaluate_rules([], FakeShortLink("deny"), "CN", "pc", None, False) == "deny"


def test_weighted_random_choice_respects_weight():
    urls = [FakeTargetUrl("a", weight=10), FakeTargetUrl("b", weight=0)]
    for _ in range(10):
        assert weighted_random_choice(urls).url == "a"


def test_weighted_random_choice_inactive_ignored():
    urls = [FakeTargetUrl("a", is_active=False), FakeTargetUrl("b")]
    assert weighted_random_choice(urls).url == "b"
