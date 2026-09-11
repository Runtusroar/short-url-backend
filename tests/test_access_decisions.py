from types import SimpleNamespace

import pytest

from app.db.models import AccessResult, BlockReason
from app.services.access import AccessContext, decide_access, evaluate_access, target_error_decision
from app.services.proxy import ProxyResult
from app.services.user_agent import ParsedUserAgent


def policy(**overrides):
    values = {
        "country_mode": "off",
        "countries": [],
        "platform_mode": "off",
        "platforms": [],
        "referer_mode": "off",
        "referer_patterns": [],
        "block_proxy": False,
        "block_bot": False,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def ua(*, platform="desktop", is_bot=False, bot_name=None):
    return ParsedUserAgent(
        raw="test-agent",
        platform=platform,
        browser=None,
        browser_version=None,
        os=None,
        os_version=None,
        device_type=None,
        brand=None,
        model=None,
        is_bot=is_bot,
        bot_name=bot_name,
    )


def context(**overrides):
    values = {
        "ip": "203.0.113.40",
        "country": "US",
        "ua": ua(),
        "referer": "https://news.example/article",
        "proxy": None,
    }
    values.update(overrides)
    return AccessContext(**values)


def test_global_blacklist_wins_over_every_later_reason():
    """Moving the blacklist below policy checks would expose blocked IPs."""
    decision = evaluate_access(
        policy(
            country_mode="allow",
            countries=["CN"],
            platform_mode="allow",
            platforms=["smartphone"],
            referer_mode="allow",
            referer_patterns=["partner.example"],
            block_proxy=True,
            block_bot=True,
        ),
        context(ua=ua(platform="bot", is_bot=True, bot_name="Googlebot"), proxy=ProxyResult(True, "vpn", "cache")),
        blacklisted="恶意请求",
    )

    assert (decision.result, decision.block_reason) == (AccessResult.BLOCKED, BlockReason.IP)
    assert decision.block_detail == "IP 命中全局黑名单：恶意请求"


async def test_bot_is_blocked_before_proxy_lookup_when_bot_blocking_is_enabled():
    """Calling the provider before applying the bot rule wastes paid lookups."""
    class Provider:
        async def lookup(self, ip: str, *, timeout: float):
            raise AssertionError("bot requests must not query the provider")

    decision = await decide_access(
        policy(block_bot=True, block_proxy=True),
        context(ua=ua(platform="bot", is_bot=True, bot_name="Googlebot")),
        blacklisted=False,
        provider=Provider(),
    )

    assert (decision.result, decision.block_reason) == (AccessResult.BLOCKED, BlockReason.BOT)
    assert decision.block_detail == "检测到机器人：Googlebot"


async def test_bot_skips_provider_and_uses_proxy_reason_when_only_proxy_blocking_is_enabled():
    """Changing bot/proxy precedence must retain the documented proxy reason."""
    class Provider:
        async def lookup(self, ip: str, *, timeout: float):
            raise AssertionError("bots do not need a proxy lookup")

    decision = await decide_access(
        policy(block_proxy=True),
        context(ua=ua(platform="bot", is_bot=True, bot_name="Googlebot")),
        blacklisted=False,
        provider=Provider(),
    )

    assert (decision.result, decision.block_reason) == (AccessResult.BLOCKED, BlockReason.PROXY)
    assert decision.block_detail == "检测到代理：bot"


def test_proxy_is_blocked_before_country_platform_and_referer():
    """Reordering proxy after dimensions would produce the wrong explanation."""
    decision = evaluate_access(
        policy(country_mode="allow", countries=["CN"], block_proxy=True),
        context(proxy=ProxyResult(True, "anonymous_vpn", "cache")),
        blacklisted=False,
    )

    assert (decision.result, decision.block_reason, decision.block_detail) == (
        AccessResult.BLOCKED,
        BlockReason.PROXY,
        "检测到代理：anonymous_vpn",
    )


@pytest.mark.parametrize(
    ("mode", "countries", "country", "detail"),
    [
        ("allow", ["CN"], "US", "国家 US 不在允许列表"),
        ("block", ["US"], "US", "国家 US 命中阻止列表"),
    ],
)
def test_country_policy_modes_report_the_matched_country(mode, countries, country, detail):
    decision = evaluate_access(policy(country_mode=mode, countries=countries), context(country=country), False)

    assert (decision.result, decision.block_reason, decision.block_detail) == (
        AccessResult.BLOCKED,
        BlockReason.COUNTRY,
        detail,
    )


@pytest.mark.parametrize(
    ("mode", "platforms", "platform", "detail"),
    [
        ("allow", ["smartphone"], "desktop", "平台 desktop 不在允许列表"),
        ("block", ["desktop"], "desktop", "平台 desktop 命中阻止列表"),
    ],
)
def test_platform_policy_modes_report_the_matched_platform(mode, platforms, platform, detail):
    decision = evaluate_access(policy(platform_mode=mode, platforms=platforms), context(ua=ua(platform=platform)), False)

    assert (decision.result, decision.block_reason, decision.block_detail) == (
        AccessResult.BLOCKED,
        BlockReason.PLATFORM,
        detail,
    )


@pytest.mark.parametrize(
    ("mode", "patterns", "referer", "detail"),
    [
        ("allow", ["partner.example"], "https://news.example/article", "Referer news.example 不在允许列表"),
        ("block", ["*.example"], "https://news.example/article", "Referer news.example 命中阻止列表"),
    ],
)
def test_referer_policy_modes_report_the_matched_host(mode, patterns, referer, detail):
    decision = evaluate_access(policy(referer_mode=mode, referer_patterns=patterns), context(referer=referer), False)

    assert (decision.result, decision.block_reason, decision.block_detail) == (
        AccessResult.BLOCKED,
        BlockReason.REFERER,
        detail,
    )


def test_allowed_decision_has_no_block_metadata():
    decision = evaluate_access(policy(), context(), False)

    assert decision == evaluate_access(policy(), context(), False)
    assert (decision.result, decision.block_reason, decision.block_detail) == (AccessResult.ALLOWED, None, None)


def test_missing_target_is_an_other_error_for_the_redirect_caller():
    """A redirect caller must retain an explicit reason when no target exists."""
    decision = target_error_decision("允许")

    assert (decision.result, decision.block_reason, decision.block_detail) == (
        AccessResult.ERROR,
        BlockReason.OTHER,
        "没有可用的允许目标 URL",
    )
