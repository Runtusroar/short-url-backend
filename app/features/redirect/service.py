from datetime import datetime, timezone
import fnmatch
import random
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError, PermissionDeniedError
from app.features.redirect.ua import get_platform
from app.integrations.maxmind.country import get_country
from app.models import AccessLog, AccessRule, Domain, IpBlacklist, ShortLink, TargetUrl


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
    active = [u for u in urls if u.is_active and u.weight > 0]
    if not active:
        return None
    weights = [u.weight for u in active]
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
            TargetUrl.is_active == True,
            TargetUrl.weight > 0,
        )
    )
    urls = result.scalars().all()
    target = weighted_random_choice(urls)
    return action, target


async def is_blacklisted(db: AsyncSession, ip: str) -> bool:
    result = await db.execute(select(IpBlacklist).where(IpBlacklist.ip == ip))
    return result.scalar_one_or_none() is not None


async def _log_access(
    db: AsyncSession,
    short_link: ShortLink,
    domain: Domain,
    target: TargetUrl | None,
    result: str,
    ip: str,
    country: str | None,
    ua_string: str | None,
    platform: str | None,
    referer: str | None,
):
    accessed_at = datetime.now(timezone.utc)
    plus8_date = accessed_at.astimezone(ZoneInfo("Asia/Shanghai")).date()
    dedup_bucket = int(accessed_at.timestamp() // 30) * 30
    log = AccessLog(
        short_link_id=short_link.id,
        domain_id=domain.id,
        target_url_id=target.id if target else None,
        result=result,
        ip=ip,
        country=country,
        ua_string=ua_string,
        ua_platform=platform,
        referer=referer,
        accessed_at=accessed_at,
        accessed_at_plus8=plus8_date,
        dedup_bucket=dedup_bucket,
    )
    db.add(log)
    await db.commit()


async def _resolve_domain(db: AsyncSession, host: str) -> Domain:
    result = await db.execute(select(Domain).where(Domain.name == host))
    domain = result.scalar_one_or_none()
    if domain and domain.is_active:
        return domain
    result = await db.execute(select(Domain).where(Domain.is_default == True))
    domain = result.scalar_one_or_none()
    if domain and domain.is_active:
        return domain
    raise NotFoundError("域名")


async def resolve_domain_and_link(
    db: AsyncSession,
    host: str,
    short_code: str,
) -> tuple[Domain, ShortLink]:
    domain = await _resolve_domain(db, host)

    result = await db.execute(
        select(ShortLink).where(
            ShortLink.domain_id == domain.id,
            ShortLink.short_code == short_code,
            ShortLink.is_active == True,
            ShortLink.deleted_at.is_(None),
        )
    )
    link = result.scalar_one_or_none()
    if not link:
        raise NotFoundError("短链")
    return domain, link


async def select_and_log_redirect(
    db: AsyncSession,
    link: ShortLink,
    domain: Domain,
    ip: str,
    country: str | None,
    ua_string: str | None,
    platform: str | None,
    referer: str | None,
    blacklisted: bool,
    is_proxy: bool,
) -> tuple[str, TargetUrl | None]:
    action, target = await get_redirect_target(
        db,
        link,
        country,
        platform,
        referer,
        blacklisted,
        is_proxy,
    )

    await _log_access(
        db,
        link,
        domain,
        target,
        action,
        ip,
        country,
        ua_string,
        platform,
        referer,
    )
    return action, target


async def execute_redirect(
    db: AsyncSession,
    host: str,
    short_code: str,
    client_ip: str,
    ua_string: str | None,
    referer: str | None,
) -> str:
    domain, link = await resolve_domain_and_link(db, host, short_code)
    blacklisted = await is_blacklisted(db, client_ip)
    country = get_country(client_ip)
    platform = get_platform(ua_string)
    is_proxy = platform == "bot"

    action, target = await select_and_log_redirect(
        db,
        link,
        domain,
        client_ip,
        country,
        ua_string,
        platform,
        referer,
        blacklisted,
        is_proxy,
    )

    if not target:
        if action in ("denied", "blocked"):
            raise PermissionDeniedError("访问被拒绝")
        raise NotFoundError("目标URL")

    return target.url
