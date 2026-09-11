"""Deterministic link-policy decisions with human-readable explanations."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AccessResult, BlockReason, LinkPolicy
from app.services.proxy import ProxyClient, ProxyResult, get_proxy_result
from app.services.user_agent import ParsedUserAgent

if TYPE_CHECKING:
    from app.db.models import IpBlacklist


@dataclass(frozen=True, slots=True)
class AccessContext:
    ip: str
    country: str | None
    ua: ParsedUserAgent | None
    referer: str | None
    proxy: ProxyResult | None = None


@dataclass(frozen=True, slots=True)
class AccessDecision:
    result: AccessResult
    block_reason: BlockReason | None = None
    block_detail: str | None = None


def _policy_value(policy: LinkPolicy | Any, name: str, default: Any) -> Any:
    return getattr(policy, name, default)


def _mode(policy: LinkPolicy | Any, name: str) -> str:
    return str(_policy_value(policy, name, "off"))


def _values(policy: LinkPolicy | Any, name: str) -> set[str]:
    return {str(value).lower() for value in (_policy_value(policy, name, []) or [])}


def _bot_name(ua: ParsedUserAgent | None) -> str:
    if ua is None:
        return "bot"
    return ua.bot_name or ua.platform or "bot"


def _referer_host(referer: str | None) -> str | None:
    if not referer:
        return None
    hostname = urlparse(referer).hostname
    return hostname.lower().rstrip(".") if hostname else None


def _referer_matches(host: str | None, patterns: set[str]) -> bool:
    if host is None:
        return False
    for pattern in patterns:
        if pattern.startswith("*."):
            suffix = pattern[1:]
            if host.endswith(suffix) and host != pattern[2:]:
                return True
        elif host == pattern:
            return True
    return False


def _blacklist_detail(blacklisted: bool | str | "IpBlacklist", ip: str) -> str:
    if isinstance(blacklisted, str):
        return f"IP 命中全局黑名单：{blacklisted}"
    reason = getattr(blacklisted, "reason", None)
    return f"IP 命中全局黑名单：{reason}" if reason else f"IP {ip} 命中全局黑名单"


def _blocked(reason: BlockReason, detail: str) -> AccessDecision:
    return AccessDecision(AccessResult.BLOCKED, reason, detail)


def evaluate_access(
    policy: LinkPolicy | Any,
    context: AccessContext,
    blacklisted: bool | str | "IpBlacklist" = False,
) -> AccessDecision:
    """Evaluate policy in its fixed precedence order.

    Target selection happens after this pure policy decision in the redirect
    caller, so missing-target failures cannot obscure one of these causes.
    """
    if blacklisted:
        return _blocked(BlockReason.IP, _blacklist_detail(blacklisted, context.ip))

    ua = context.ua
    is_bot = bool(ua and ua.is_bot)
    if is_bot and _policy_value(policy, "block_bot", False):
        return _blocked(BlockReason.BOT, f"检测到机器人：{_bot_name(ua)}")

    if is_bot and _policy_value(policy, "block_proxy", False):
        return _blocked(BlockReason.PROXY, "检测到代理：bot")
    if context.proxy is not None and context.proxy.is_proxy and _policy_value(policy, "block_proxy", False):
        return _blocked(BlockReason.PROXY, f"检测到代理：{context.proxy.proxy_type or 'unknown'}")

    country = (context.country or "未知").upper()
    countries = _values(policy, "countries")
    country_mode = _mode(policy, "country_mode")
    if country_mode == "allow" and country.lower() not in countries:
        return _blocked(BlockReason.COUNTRY, f"国家 {country} 不在允许列表")
    if country_mode == "block" and country.lower() in countries:
        return _blocked(BlockReason.COUNTRY, f"国家 {country} 命中阻止列表")

    platform = ua.platform if ua is not None else "other"
    platforms = _values(policy, "platforms")
    platform_mode = _mode(policy, "platform_mode")
    if platform_mode == "allow" and platform.lower() not in platforms:
        return _blocked(BlockReason.PLATFORM, f"平台 {platform} 不在允许列表")
    if platform_mode == "block" and platform.lower() in platforms:
        return _blocked(BlockReason.PLATFORM, f"平台 {platform} 命中阻止列表")

    host = _referer_host(context.referer)
    display_host = host or "缺失"
    patterns = _values(policy, "referer_patterns")
    matches_referer = _referer_matches(host, patterns)
    referer_mode = _mode(policy, "referer_mode")
    if referer_mode == "allow" and not matches_referer:
        return _blocked(BlockReason.REFERER, f"Referer {display_host} 不在允许列表")
    if referer_mode == "block" and matches_referer:
        return _blocked(BlockReason.REFERER, f"Referer {display_host} 命中阻止列表")

    return AccessDecision(AccessResult.ALLOWED)


async def decide_access(
    policy: LinkPolicy | Any,
    context: AccessContext,
    *,
    blacklisted: bool | str | "IpBlacklist" = False,
    provider: ProxyClient | None = None,
    db: AsyncSession | None = None,
    now: datetime | Callable[[], datetime] | None = None,
) -> AccessDecision:
    """Resolve optional proxy intelligence without violating decision order."""
    preliminary = evaluate_access(policy, context, blacklisted)
    if preliminary.block_reason in {BlockReason.IP, BlockReason.BOT, BlockReason.PROXY}:
        return preliminary
    if (
        _policy_value(policy, "block_proxy", False)
        and provider is not None
        and db is not None
        and now is not None
    ):
        proxy = await get_proxy_result(db, context.ip, provider, now)
        return evaluate_access(policy, replace(context, proxy=proxy), blacklisted)
    return preliminary


def target_error_decision(target_kind: str) -> AccessDecision:
    """Describe a missing redirect destination after policy evaluation."""
    return AccessDecision(AccessResult.ERROR, BlockReason.OTHER, f"没有可用的{target_kind}目标 URL")
