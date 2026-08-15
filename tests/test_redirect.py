from app.models import AccessRule
from app.services.redirect import evaluate_rules, rule_matches, weighted_random_choice


class FakeTargetUrl:
    def __init__(self, url, weight=1, is_active=True):
        self.url = url
        self.weight = weight
        self.is_active = is_active


def _rule(**kwargs):
    defaults = {"is_active": True}
    defaults.update(kwargs)
    return AccessRule(**defaults)


def test_rule_matches_country():
    rule = _rule(countries=["CN"], ua_platforms=[], referer_pattern=None)
    assert rule_matches(rule, "CN", "pc", "https://example.com") is True
    assert rule_matches(rule, "US", "pc", "https://example.com") is False


def test_rule_matches_platform():
    rule = _rule(countries=[], ua_platforms=["mobile"], referer_pattern=None)
    assert rule_matches(rule, "CN", "mobile", None) is True
    assert rule_matches(rule, "CN", "pc", None) is False


def test_evaluate_rules_priority():
    rule1 = _rule(action="deny", priority=0, countries=["CN"])
    rule2 = _rule(action="allow", priority=1, countries=["CN"])
    assert evaluate_rules([rule1, rule2], "CN", "pc", None) == "deny"


def test_evaluate_rules_default_allow():
    assert evaluate_rules([], "CN", "pc", None) == "allow"


def test_weighted_random_choice_respects_weight():
    urls = [FakeTargetUrl("a", weight=10), FakeTargetUrl("b", weight=0)]
    for _ in range(10):
        assert weighted_random_choice(urls).url == "a"


def test_weighted_random_choice_inactive_ignored():
    urls = [FakeTargetUrl("a", is_active=False), FakeTargetUrl("b")]
    assert weighted_random_choice(urls).url == "b"
