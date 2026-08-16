import fnmatch
import ipaddress
import random
from dataclasses import dataclass
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import cast, or_, select
from sqlalchemy.dialects.postgresql import INET
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError, PermissionDeniedError
from app.features.redirect.ua import get_platform
from app.features.short_links.short_code import normalize_short_code
from app.integrations.maxmind.country import get_country
from app.models import AccessLog, AccessRule, Domain, IpBlacklist, ShortLink, TargetUrl
from app.models.enums import (
    AccessAction,
    ClientRequirement,
    DecisionReason,
    ProxyRequirement,
    RedirectResult,
)


@dataclass(frozen=True)
class RedirectDecision:
    result: RedirectResult
    reason: DecisionReason
    matched_rule: AccessRule | None
    target: TargetUrl | None


def _match_list(value: str | None, candidates: list | None) -> bool:
    if not candidates:
        return True
    if value is None:
        return False
    return value.lower() in {c.lower() for c in candidates}


def _match_referer(referer: str | None, patterns: list[str] | None) -> bool:
    if not patterns:
        return True
    if referer is None:
        return False
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
    client_requirement = ClientRequirement(rule.client_requirement)
    if client_requirement == ClientRequirement.HUMAN and platform == "bot":
        return False
    if client_requirement == ClientRequirement.BOT and platform != "bot":
        return False
    if not _match_list(platform, rule.ua_platforms):
        return False
    proxy_requirement = ProxyRequirement(rule.proxy_requirement)
    if proxy_requirement == ProxyRequirement.PROXY and not is_proxy:
        return False
    if proxy_requirement == ProxyRequirement.NON_PROXY and is_proxy:
        return False
    return _match_referer(referer, rule.referer_patterns)


def evaluate_rules(
    rules: list[AccessRule],
    short_link: ShortLink,
    country: str | None,
    platform: str | None,
    referer: str | None,
    is_proxy: bool,
) -> tuple[AccessAction, AccessRule | None]:
    active_rules = [r for r in rules if r.is_active]
    active_rules.sort(key=lambda rule: (rule.priority, rule.id))
    for rule in active_rules:
        if rule_matches(rule, country, platform, referer, is_proxy):
            return AccessAction(rule.action), rule
    return AccessAction(short_link.default_action), None


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
) -> RedirectDecision:
    if is_blacklisted:
        redirect_result = RedirectResult.BLOCKED
        reason = DecisionReason.BLACKLIST
        matched_rule = None
    else:
        result = await db.execute(
            select(AccessRule)
            .where(
                AccessRule.short_link_id == short_link.id,
                AccessRule.is_active == True,
            )
            .order_by(AccessRule.priority, AccessRule.id)
            .with_for_update(key_share=True)
        )
        rules = result.scalars().all()
        action, matched_rule = evaluate_rules(
            rules, short_link, country, platform, referer, is_proxy
        )
        redirect_result = (
            RedirectResult.ALLOWED
            if action == AccessAction.ALLOW
            else RedirectResult.DENIED
        )
        reason = (
            DecisionReason.MATCHED_RULE
            if matched_rule
            else DecisionReason.DEFAULT_ACTION
        )

    url_type = (
        "denied"
        if redirect_result in (RedirectResult.DENIED, RedirectResult.BLOCKED)
        else "allowed"
    )
    result = await db.execute(
        select(TargetUrl).where(
            TargetUrl.short_link_id == short_link.id,
            TargetUrl.url_type == url_type,
            TargetUrl.is_active == True,
            TargetUrl.weight > 0,
        ).with_for_update(key_share=True)
    )
    urls = result.scalars().all()
    target = weighted_random_choice(urls)
    if target is None:
        reason = DecisionReason.NO_TARGET
    return RedirectDecision(redirect_result, reason, matched_rule, target)


async def is_blacklisted(db: AsyncSession, ip: str | None) -> bool:
    try:
        canonical_ip = str(ipaddress.ip_address(ip))
    except (TypeError, ValueError):
        return False
    now = datetime.now(UTC)
    result = await db.execute(
        select(IpBlacklist).where(
            IpBlacklist.ip == cast(canonical_ip, INET),
            IpBlacklist.removed_at.is_(None),
            or_(IpBlacklist.expires_at.is_(None), IpBlacklist.expires_at > now),
        )
    )
    return result.scalar_one_or_none() is not None


async def _log_access(
    db: AsyncSession,
    short_link: ShortLink,
    domain: Domain,
    decision: RedirectDecision,
    client_ip: str,
    country: str | None,
    user_agent: str | None,
    platform: str | None,
    referer: str | None,
    request_host: str | None,
    request_method: str,
):
    accessed_at = datetime.now(UTC)
    access_date = accessed_at.astimezone(ZoneInfo(domain.timezone)).date()
    dedup_bucket = int(accessed_at.timestamp() // 30) * 30
    try:
        normalized_client_ip = str(ipaddress.ip_address(client_ip))
    except (TypeError, ValueError):
        normalized_client_ip = None
    is_bot = platform == "bot"
    log = AccessLog(
        short_link_id=short_link.id,
        domain_id=domain.id,
        target_url_id=decision.target.id if decision.target else None,
        matched_rule_id=decision.matched_rule.id if decision.matched_rule else None,
        result=decision.result,
        client_ip=normalized_client_ip,
        country=country,
        user_agent=user_agent,
        ua_platform=platform,
        referer=referer,
        accessed_at=accessed_at,
        access_date=access_date,
        dedup_bucket=dedup_bucket,
        decision_reason=decision.reason,
        matched_rule_name=decision.matched_rule.name if decision.matched_rule else None,
        target_url_snapshot=decision.target.url if decision.target else None,
        request_host=request_host,
        request_method=request_method,
        proxy_check_status="assumed_bot" if is_bot else "skipped",
        is_anonymous=is_bot,
        proxy_types=[],
        proxy_source="assumed_bot" if is_bot else None,
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
    short_code = normalize_short_code(short_code)

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
    request_host: str | None,
    request_method: str,
) -> RedirectDecision:
    decision = await get_redirect_target(
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
        decision,
        ip,
        country,
        ua_string,
        platform,
        referer,
        request_host,
        request_method,
    )
    return decision


async def execute_redirect(
    db: AsyncSession,
    host: str,
    short_code: str,
    client_ip: str,
    ua_string: str | None,
    referer: str | None,
    request_method: str = "GET",
) -> str:
    domain, link = await resolve_domain_and_link(db, host, short_code)
    blacklisted = await is_blacklisted(db, client_ip)
    country = get_country(client_ip)
    platform = get_platform(ua_string)
    is_proxy = platform == "bot"

    decision = await select_and_log_redirect(
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
        host,
        request_method,
    )

    if not decision.target:
        if decision.result in (RedirectResult.DENIED, RedirectResult.BLOCKED):
            raise PermissionDeniedError("访问被拒绝")
        raise NotFoundError("目标URL")

    return decision.target.url
