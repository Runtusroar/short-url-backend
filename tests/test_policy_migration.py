from dataclasses import dataclass

import pytest

from app.services.policy_migration import convert_legacy_policy


@dataclass
class LegacyRule:
    action: str = "deny"
    priority: int = 0
    countries: list[str] | None = None
    ua_platforms: list[str] | None = None
    referer_pattern: str | None = None
    allow_proxy: bool = True
    allow_bot: bool = True
    is_active: bool = True


def legacy_rule(**kwargs) -> LegacyRule:
    return LegacyRule(**kwargs)


def test_empty_rules_keep_an_unrestricted_link_unrestricted():
    """Adding a policy for an empty allow-default link would change its traffic."""
    result = convert_legacy_policy(default_action="allow", rules=[])

    assert result.convertible is True
    assert result.policy is None


@pytest.mark.parametrize(
    ("default_action", "action", "countries", "expected_mode"),
    [
        ("allow", "deny", ["CN", "US"], "block"),
        ("deny", "allow", ["CN", "US"], "allow"),
    ],
)
def test_country_rule_with_opposite_default_becomes_equivalent_country_policy(
    default_action, action, countries, expected_mode
):
    """Reversing country mode would invert allowed traffic."""
    result = convert_legacy_policy(
        default_action=default_action,
        rules=[legacy_rule(action=action, countries=countries)],
    )

    assert result.convertible is True
    assert result.policy.country_mode == expected_mode
    assert result.policy.countries == countries


def test_platform_rule_with_opposite_default_becomes_equivalent_platform_policy():
    """A legacy mobile allowlist must use the normalized smartphone value."""
    result = convert_legacy_policy(
        default_action="deny",
        rules=[legacy_rule(action="allow", ua_platforms=["mobile"], allow_bot=False)],
    )

    assert result.convertible is True
    assert result.policy.platform_mode == "allow"
    assert result.policy.platforms == ["smartphone"]
    assert result.policy.block_bot is True


def test_platform_allow_rule_with_legacy_bot_bypass_refuses_conversion():
    """A bot matched every legacy platform rule, unlike a normalized allowlist."""
    result = convert_legacy_policy(
        default_action="deny",
        rules=[legacy_rule(action="allow", ua_platforms=["mobile"], allow_bot=True)],
    )

    assert result.convertible is False
    assert "bot" in result.reason


def test_country_values_normalize_case_without_changing_membership():
    """Case-insensitive legacy country matching must become uppercase ISO-style values."""
    result = convert_legacy_policy(
        default_action="deny",
        rules=[legacy_rule(action="allow", countries=["cn", "uS"])],
    )

    assert result.convertible is True
    assert result.policy.countries == ["CN", "US"]


def test_non_ascii_legacy_country_refuses_conversion():
    """Uppercasing a non-ASCII token can change its legacy membership semantics."""
    result = convert_legacy_policy(
        default_action="deny",
        rules=[legacy_rule(action="allow", countries=["ſs"])],
    )

    assert result.convertible is False
    assert "countries" in result.reason


def test_legacy_referer_url_glob_refuses_conversion():
    """A full-URL legacy glob cannot be replaced by hostname policy semantics."""
    result = convert_legacy_policy(
        default_action="deny",
        rules=[legacy_rule(action="allow", referer_pattern="https://example.test/*")],
    )

    assert result.convertible is False
    assert "referer" in result.reason


def test_unconditional_legacy_deny_refuses_conversion():
    """An all-traffic legacy deny would be reversed by an off-dimension policy."""
    result = convert_legacy_policy(
        default_action="allow",
        rules=[legacy_rule(action="deny")],
    )

    assert result.convertible is False
    assert "unconditional deny" in result.reason


@pytest.mark.parametrize(
    ("field", "legacy_value", "expected_value"),
    [
        ("allow_proxy", False, True),
        ("allow_bot", False, True),
    ],
)
def test_proxy_and_bot_flags_become_explicit_blocks(field, legacy_value, expected_value):
    """Ignoring a legacy false flag would allow proxy or bot traffic."""
    result = convert_legacy_policy(
        default_action="deny",
        rules=[legacy_rule(action="allow", **{field: legacy_value})],
    )

    assert result.convertible is True
    assert getattr(result.policy, f"block_{field.removeprefix('allow_')}") is expected_value


def test_complex_legacy_rule_refuses_conversion():
    """Splitting AND conditions into policy dimensions broadens legacy matches."""
    result = convert_legacy_policy(
        default_action="deny",
        rules=[legacy_rule(action="allow", countries=["CN"], ua_platforms=["mobile"])],
    )

    assert result.convertible is False
    assert "combined conditions" in result.reason


def test_conflicting_ordered_rules_refuse_conversion():
    """A single policy cannot represent conflicting first-match ordered rules."""
    result = convert_legacy_policy(
        default_action="allow",
        rules=[
            legacy_rule(action="deny", priority=10, countries=["CN"]),
            legacy_rule(action="allow", priority=20, countries=["CN"]),
        ],
    )

    assert result.convertible is False
    assert "ordered" in result.reason
