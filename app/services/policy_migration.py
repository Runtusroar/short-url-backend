"""Conservative conversion of ordered legacy rules to one link policy.

The legacy evaluator is first-match ordered.  A normalized policy is not, so
this module deliberately rejects any rule set it cannot prove equivalent.
"""

from dataclasses import dataclass, field
from typing import Any, Iterable


@dataclass(frozen=True, slots=True)
class ConvertedPolicy:
    country_mode: str = "off"
    countries: list[str] = field(default_factory=list)
    platform_mode: str = "off"
    platforms: list[str] = field(default_factory=list)
    referer_mode: str = "off"
    referer_patterns: list[str] = field(default_factory=list)
    block_proxy: bool = False
    block_bot: bool = False


@dataclass(frozen=True, slots=True)
class PolicyConversion:
    convertible: bool
    policy: ConvertedPolicy | None = None
    reason: str | None = None
    affected_ids: tuple[str, ...] = ()


def _value(rule: Any, name: str, default: Any = None) -> Any:
    if isinstance(rule, dict):
        return rule.get(name, default)
    return getattr(rule, name, default)


def _link_id(link: Any) -> tuple[str, ...]:
    link_id = _value(link, "id") if link is not None else None
    return (str(link_id),) if link_id is not None else ()


def _refusal(link: Any, reason: str) -> PolicyConversion:
    return PolicyConversion(False, reason=reason, affected_ids=_link_id(link))


def convert_legacy_policy(
    link: Any = None,
    rules: Iterable[Any] = (),
    *,
    default_action: str | None = None,
) -> PolicyConversion:
    """Return one equivalent policy or a reason conversion must stop.

    Only a single active rule whose action is the inverse of the legacy
    default can be mapped.  The rule may constrain one traffic dimension and
    may additionally reject proxy and bot requests on an allow-defaulted
    exception.  Everything else is refused rather than approximated.
    """
    if default_action is None:
        default_action = _value(link, "default_action")
    if default_action not in {"allow", "deny"}:
        return _refusal(link, "legacy default action is missing or unsupported")

    active_rules = [rule for rule in rules if _value(rule, "is_active", True)]
    active_rules.sort(key=lambda rule: (_value(rule, "priority", 0), str(_value(rule, "id", ""))))
    if not active_rules:
        if default_action == "allow":
            return PolicyConversion(True)
        return _refusal(link, "default deny without a rule cannot be represented by a policy")
    if len(active_rules) != 1:
        return _refusal(link, "ordered legacy rules cannot be represented by one policy")

    rule = active_rules[0]
    action = _value(rule, "action")
    if {default_action, action} != {"allow", "deny"}:
        return _refusal(link, "rule action does not form a single exception to the default")

    countries = list(_value(rule, "countries", []) or [])
    platforms = list(_value(rule, "ua_platforms", []) or [])
    referer_pattern = _value(rule, "referer_pattern")
    referer_patterns = [part.strip() for part in (referer_pattern or "").split(",") if part.strip()]
    dimensions = sum(bool(value) for value in (countries, platforms, referer_patterns))
    if dimensions > 1:
        return _refusal(link, "combined conditions cannot be represented without changing behavior")

    allow_proxy = _value(rule, "allow_proxy", True)
    allow_bot = _value(rule, "allow_bot", True)
    if (not allow_proxy or not allow_bot) and action != "allow":
        return _refusal(link, "proxy or bot exceptions cannot preserve a deny rule")

    mode = "allow" if action == "allow" else "block"
    policy = ConvertedPolicy(
        country_mode=mode if countries else "off",
        countries=countries,
        platform_mode=mode if platforms else "off",
        platforms=platforms,
        referer_mode=mode if referer_patterns else "off",
        referer_patterns=referer_patterns,
        block_proxy=not allow_proxy,
        block_bot=not allow_bot,
    )
    return PolicyConversion(True, policy=policy)


def find_unconvertible_links(links_and_rules: Iterable[tuple[Any, Iterable[Any]]]) -> list[PolicyConversion]:
    """Convert every link and return only failures, each with its source ID."""
    failures = []
    for link, rules in links_and_rules:
        conversion = convert_legacy_policy(link, rules)
        if not conversion.convertible:
            failures.append(conversion)
    return failures
