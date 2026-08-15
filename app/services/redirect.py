import fnmatch
import random
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AccessRule, ShortLink, TargetUrl


def _match_list(value: str | None, candidates: list | None) -> bool:
    if not candidates:
        return True
    if value is None:
        return False
    return value.lower() in {c.lower() for c in candidates}


def _match_referer(referer: str | None, pattern: str | None) -> bool:
    if not pattern:
        return True
    if referer is None:
        return False
    patterns = [p.strip() for p in pattern.split(",") if p.strip()]
    return any(fnmatch.fnmatch(referer, p) for p in patterns)


def rule_matches(
    rule: AccessRule,
    country: str | None,
    platform: str | None,
    referer: str | None,
    is_proxy: bool,
) -> bool:
    if not _match_list(country, rule.countries):
        return False
    if platform == "bot" and not rule.allow_bot:
        return False
    if platform != "bot" and not _match_list(platform, rule.ua_platforms):
        return False
    if is_proxy and not rule.allow_proxy:
        return False
    if not _match_referer(referer, rule.referer_pattern):
        return False
    return True


def evaluate_rules(
    rules: list[AccessRule],
    short_link: ShortLink,
    country: str | None,
    platform: str | None,
    referer: str | None,
    is_proxy: bool,
) -> str:
    active_rules = [r for r in rules if r.is_active]
    active_rules.sort(key=lambda r: r.priority)
    for rule in active_rules:
        if rule_matches(rule, country, platform, referer, is_proxy):
            return rule.action
    return short_link.default_action


def weighted_random_choice(urls: list[TargetUrl]) -> TargetUrl | None:
    active = [u for u in urls if u.is_active]
    if not active:
        return None
    weights = [u.weight for u in active]
    total = sum(weights)
    if total <= 0:
        return random.choice(active)
    return random.choices(active, weights=weights, k=1)[0]


async def get_redirect_target(
    db: AsyncSession,
    short_link: ShortLink,
    country: str | None,
    platform: str | None,
    referer: str | None,
    is_blacklisted: bool,
    is_proxy: bool,
) -> tuple[str, UUID | None]:
    if is_blacklisted:
        action = "blocked"
    else:
        result = await db.execute(select(AccessRule).where(AccessRule.short_link_id == short_link.id))
        rules = result.scalars().all()
        action = evaluate_rules(rules, short_link, country, platform, referer, is_proxy)
        if action == "allow":
            action = "allowed"
        elif action == "deny":
            action = "denied"

    url_type = "denied" if action in ("denied", "blocked") else "allowed"
    result = await db.execute(
        select(TargetUrl).where(
            TargetUrl.short_link_id == short_link.id,
            TargetUrl.url_type == url_type,
        )
    )
    urls = result.scalars().all()
    target = weighted_random_choice(urls)
    return action, target
