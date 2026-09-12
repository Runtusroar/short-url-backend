from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import uuid

import pytest
from sqlalchemy import cast, select
from sqlalchemy.dialects.postgresql import INET
from sqlalchemy.exc import SQLAlchemyError

from app.db import AsyncSessionLocal
from app.db.models import AccessLog, Domain, IpBlacklist, IpReputation, LinkPolicy, ShortLink, TargetUrl
from app.services.access_log import AccessLogSnapshot, write_access_log


async def _make_redirect_link(
    *,
    host: str | None = None,
    short_code: str = "CampaignA",
    domain_active: bool = True,
    link_active: bool = True,
    destinations: list[tuple[str, str, int, bool]] | None = None,
    policy: dict[str, object] | None = None,
) -> tuple[Domain, ShortLink]:
    """Create committed route data because the app handles requests in another session."""
    async with AsyncSessionLocal() as session:
        domain = Domain(name=host or f"{uuid.uuid4().hex}.example", is_active=domain_active)
        session.add(domain)
        await session.flush()
        link = ShortLink(
            domain_id=domain.id,
            short_code=short_code,
            note="redirect integration fixture",
            is_active=link_active,
        )
        session.add(link)
        await session.flush()
        policy_values = {
            "country_mode": "off",
            "countries": [],
            "platform_mode": "off",
            "platforms": [],
            "referer_mode": "off",
            "referer_patterns": [],
            "block_proxy": False,
            "block_bot": False,
        }
        policy_values.update(policy or {})
        session.add(LinkPolicy(short_link_id=link.id, **policy_values))
        for url, url_type, weight, is_active in destinations or [
            ("https://allowed.example/landing", "allowed", 1, True),
            ("https://blocked.example/landing", "blocked", 1, True),
        ]:
            session.add(
                TargetUrl(
                    short_link_id=link.id,
                    url=url,
                    url_type=url_type,
                    weight=weight,
                    is_active=is_active,
                )
            )
        await session.commit()
        return domain, link


async def _latest_log(short_link_id):
    async with AsyncSessionLocal() as session:
        return await session.scalar(
            select(AccessLog)
            .where(AccessLog.short_link_id == short_link_id)
            .order_by(AccessLog.accessed_at.desc(), AccessLog.id.desc())
        )


@pytest.mark.asyncio
async def test_unknown_or_inactive_hosts_and_links_do_not_redirect(client):
    """Resolving an unknown/inactive record would expose a disabled redirect."""
    unknown = await client.get("/CampaignA", headers={"Host": "unknown.example"}, follow_redirects=False)
    assert unknown.status_code == 404

    inactive_domain, _ = await _make_redirect_link(host=f"{uuid.uuid4().hex}.example", domain_active=False)
    response = await client.get("/CampaignA", headers={"Host": inactive_domain.name}, follow_redirects=False)
    assert response.status_code == 404

    active_domain, _ = await _make_redirect_link(host=f"{uuid.uuid4().hex}.example", link_active=False)
    response = await client.get("/CampaignA", headers={"Host": active_domain.name}, follow_redirects=False)
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_redirect_uses_trusted_forwarding_and_writes_an_immutable_bot_snapshot(client, monkeypatch):
    """Dropping forwarded values or parsed bot facts would make audit logs misleading."""
    monkeypatch.setattr("app.services.request_metadata.settings.trust_proxy_headers", True)
    domain, link = await _make_redirect_link(host=f"{uuid.uuid4().hex}.example", policy={"block_bot": True})

    response = await client.get(
        "/CampaignA",
        headers={
            "Host": "origin.example",
            "X-Real-IP": "203.0.113.24",
            "X-Forwarded-Proto": "https",
            "X-Forwarded-Host": domain.name,
            "User-Agent": "Googlebot/2.1 (+http://www.google.com/bot.html)",
        },
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert response.headers["location"] == "https://blocked.example/landing"
    log = await _latest_log(link.id)
    assert log is not None
    assert log.request_url == f"https://{domain.name}/CampaignA"
    assert str(log.ip) == "203.0.113.24"
    assert log.result == "blocked"
    assert log.block_reason == "bot"
    assert log.ua_bot_name == "Googlebot"
    assert response.content == b""


@pytest.mark.asyncio
async def test_redirect_ignores_spoofed_forwarding_when_proxy_trust_is_disabled(client, monkeypatch):
    """Accepting forwarding without trust would let callers forge routing and log IPs."""
    monkeypatch.setattr("app.services.request_metadata.settings.trust_proxy_headers", False)
    domain, link = await _make_redirect_link(host=f"{uuid.uuid4().hex}.example")

    response = await client.get(
        "/CampaignA",
        headers={
            "Host": domain.name,
            "X-Real-IP": "203.0.113.24",
            "X-Forwarded-For": "198.51.100.77",
            "X-Forwarded-Proto": "https",
            "X-Forwarded-Host": "spoofed.example",
        },
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert response.headers["location"] == "https://allowed.example/landing"
    log = await _latest_log(link.id)
    assert log is not None
    assert str(log.ip) == "127.0.0.1"
    assert log.request_url == f"http://{domain.name}/CampaignA"


@pytest.mark.asyncio
async def test_redirect_releases_its_request_transaction_before_proxy_resolution(client, monkeypatch):
    """Holding the route session across MaxMind makes a slow provider exhaust DB connections."""
    import app.api.redirect as redirect_api
    from app.db.models import AccessResult
    from app.services.access import AccessDecision

    domain, _ = await _make_redirect_link(host=f"{uuid.uuid4().hex}.example", policy={"block_proxy": True})
    resolve_domain = redirect_api._resolve_domain
    route_session = None

    async def capture_route_session(db, request):
        nonlocal route_session
        route_session = db
        return await resolve_domain(db, request)

    async def verify_released_transaction(*_args, **_kwargs):
        assert route_session is not None
        assert route_session.in_transaction() is False
        return AccessDecision(AccessResult.ALLOWED)

    monkeypatch.setattr(redirect_api, "_resolve_domain", capture_route_session)
    monkeypatch.setattr(redirect_api, "decide_access", verify_released_transaction)

    response = await client.get("/CampaignA", headers={"Host": domain.name}, follow_redirects=False)

    assert response.status_code == 302
    assert response.headers["location"] == "https://allowed.example/landing"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("policy", "headers", "country", "blacklisted_ip", "reason"),
    [
        ({"block_bot": True}, {"User-Agent": "Googlebot/2.1 (+http://www.google.com/bot.html)"}, None, None, "bot"),
        ({"block_proxy": True}, {"User-Agent": "Googlebot/2.1 (+http://www.google.com/bot.html)"}, None, None, "proxy"),
        ({"country_mode": "allow", "countries": ["CN"]}, {}, "US", None, "country"),
        ({"platform_mode": "allow", "platforms": ["smartphone"]}, {"User-Agent": "Mozilla/5.0"}, None, None, "platform"),
        ({"referer_mode": "allow", "referer_patterns": ["partner.example"]}, {"Referer": "https://other.example/page"}, None, None, "referer"),
        ({}, {"X-Real-IP": "203.0.113.99"}, None, "203.0.113.99", "ip"),
    ],
)
async def test_each_access_block_reason_selects_the_blocked_target(
    client, monkeypatch, policy, headers, country, blacklisted_ip, reason
):
    """Changing any policy branch must not send blocked traffic to the allowed target."""
    domain, link = await _make_redirect_link(host=f"{uuid.uuid4().hex}.example", policy=policy)
    monkeypatch.setattr("app.services.request_metadata.get_country", lambda _ip: country)
    if blacklisted_ip:
        monkeypatch.setattr("app.services.request_metadata.settings.trust_proxy_headers", True)
        async with AsyncSessionLocal() as session:
            session.add(IpBlacklist(ip=blacklisted_ip, reason="route test"))
            await session.commit()

    response = await client.get(
        "/CampaignA", headers={"Host": domain.name, **headers}, follow_redirects=False
    )

    assert response.status_code == 302
    assert response.headers["location"] == "https://blocked.example/landing"
    log = await _latest_log(link.id)
    assert log is not None
    assert log.result == "blocked"
    assert log.block_reason == reason


@pytest.mark.asyncio
async def test_malformed_referer_is_logged_as_a_structured_referer_decision(client):
    """A malformed optional Referer must be handled as a missing allowlist value, not as a 500."""
    domain, link = await _make_redirect_link(
        host=f"{uuid.uuid4().hex}.example",
        policy={"referer_mode": "allow", "referer_patterns": ["partner.example"]},
    )

    response = await client.get(
        "/CampaignA",
        headers={"Host": domain.name, "Referer": "http://[malformed"},
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert response.headers["location"] == "https://blocked.example/landing"
    log = await _latest_log(link.id)
    assert (log.result, log.block_reason, log.block_detail) == (
        "blocked",
        "referer",
        "Referer 缺失 不在允许列表",
    )


@pytest.mark.asyncio
async def test_missing_allowed_or_blocked_target_is_logged_as_an_explicit_error(client, monkeypatch):
    """A missing target must not erase the policy decision that selected its class."""
    allowed_domain, allowed_link = await _make_redirect_link(
        host=f"{uuid.uuid4().hex}.example",
        destinations=[("https://blocked.example/landing", "blocked", 1, True)],
    )
    allowed_response = await client.get(
        "/CampaignA", headers={"Host": allowed_domain.name}, follow_redirects=False
    )
    assert allowed_response.status_code == 404
    allowed_log = await _latest_log(allowed_link.id)
    assert (allowed_log.result, allowed_log.block_reason, allowed_log.block_detail) == (
        "error",
        "other",
        "没有可用的允许目标 URL",
    )

    blocked_domain, blocked_link = await _make_redirect_link(
        host=f"{uuid.uuid4().hex}.example",
        policy={"country_mode": "allow", "countries": ["CN"]},
        destinations=[("https://allowed.example/landing", "allowed", 1, True)],
    )
    monkeypatch.setattr("app.services.request_metadata.get_country", lambda _ip: "US")
    blocked_response = await client.get(
        "/CampaignA", headers={"Host": blocked_domain.name}, follow_redirects=False
    )
    assert blocked_response.status_code == 404
    blocked_log = await _latest_log(blocked_link.id)
    assert (blocked_log.result, blocked_log.block_reason, blocked_log.block_detail) == (
        "error",
        "other",
        "没有可用的阻止目标 URL",
    )


@pytest.mark.asyncio
async def test_head_matches_get_redirect_headers_without_a_response_body(client):
    """A HEAD request that emits a body or loses Location violates redirect semantics."""
    domain, _ = await _make_redirect_link(host=f"{uuid.uuid4().hex}.example")

    response = await client.head("/CampaignA", headers={"Host": domain.name}, follow_redirects=False)

    assert response.status_code == 302
    assert response.headers["location"] == "https://allowed.example/landing"
    assert response.content == b""


@pytest.mark.asyncio
async def test_log_storage_failure_does_not_change_the_redirect_response(client, monkeypatch):
    """A failed audit insert must not turn a valid redirect into a 500 response."""
    domain, _ = await _make_redirect_link(host=f"{uuid.uuid4().hex}.example")

    class FailingSession:
        def __init__(self):
            self.rolled_back = False

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        def add(self, _entry):
            return None

        async def commit(self):
            raise SQLAlchemyError("database unavailable")

        async def rollback(self):
            self.rolled_back = True


    failing_session = FailingSession()
    monkeypatch.setattr("app.api.redirect.AsyncSessionLocal", lambda: failing_session)

    response = await client.get("/CampaignA", headers={"Host": domain.name}, follow_redirects=False)

    assert response.status_code == 302
    assert response.headers["location"] == "https://allowed.example/landing"
    assert failing_session.rolled_back is True


@dataclass
class Provider:
    response: object | None = None
    error: Exception | None = None

    def __post_init__(self):
        self.calls: list[tuple[str, float]] = []

    async def lookup(self, ip: str, *, timeout: float):
        self.calls.append((ip, timeout))
        if self.error is not None:
            raise self.error
        return self.response


async def _reputation(ip: str):
    async with AsyncSessionLocal() as session:
        return await session.scalar(select(IpReputation).where(IpReputation.ip == cast(ip, INET)))


@pytest.mark.asyncio
async def test_non_bot_proxy_cache_hit_blocks_without_calling_the_provider(client, monkeypatch):
    """Skipping cached proxy facts would make paid lookups and routing inconsistent."""
    ip = "203.0.113.141"
    domain, link = await _make_redirect_link(
        host=f"{uuid.uuid4().hex}.example", policy={"block_proxy": True}
    )
    async with AsyncSessionLocal() as session:
        now = datetime.now(timezone.utc)
        session.add(
            IpReputation(
                ip=ip,
                is_proxy=True,
                proxy_type="anonymous_vpn",
                checked_at=now - timedelta(minutes=1),
                expires_at=now + timedelta(hours=1),
            )
        )
        await session.commit()
    provider = Provider(error=AssertionError("cache hit must not query provider"))
    monkeypatch.setattr("app.services.request_metadata.settings.trust_proxy_headers", True)
    monkeypatch.setattr("app.api.redirect.get_proxy_provider", lambda: provider)

    response = await client.get(
        "/CampaignA", headers={"Host": domain.name, "X-Real-IP": ip}, follow_redirects=False
    )

    assert response.headers["location"] == "https://blocked.example/landing"
    assert provider.calls == []
    assert (await _latest_log(link.id)).block_reason == "proxy"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("response", "expected_location", "expected_proxy"),
    [
        ({"is_proxy": True, "proxy_type": "anonymous_vpn"}, "https://blocked.example/landing", True),
        ({"is_proxy": False, "proxy_type": None}, "https://allowed.example/landing", False),
    ],
)
async def test_non_bot_fresh_proxy_reputation_is_persisted_and_applied(
    client, monkeypatch, response, expected_location, expected_proxy
):
    """Ignoring fresh provider facts would make the block_proxy policy ineffective."""
    ip = f"203.0.113.{150 if expected_proxy else 151}"
    domain, link = await _make_redirect_link(
        host=f"{uuid.uuid4().hex}.example", policy={"block_proxy": True}
    )
    provider = Provider(response=response)
    monkeypatch.setattr("app.services.request_metadata.settings.trust_proxy_headers", True)
    monkeypatch.setattr("app.api.redirect.get_proxy_provider", lambda: provider)

    response = await client.get(
        "/CampaignA", headers={"Host": domain.name, "X-Real-IP": ip}, follow_redirects=False
    )

    assert response.headers["location"] == expected_location
    assert provider.calls == [(ip, 1.5)]
    assert (await _reputation(ip)).is_proxy is expected_proxy
    assert (await _latest_log(link.id)).result == ("blocked" if expected_proxy else "allowed")


@pytest.mark.asyncio
async def test_non_bot_proxy_provider_failure_fails_open_and_is_not_persisted(client, monkeypatch):
    """Treating an unavailable provider as a proxy would block otherwise valid visitors."""
    ip = "203.0.113.152"
    domain, link = await _make_redirect_link(
        host=f"{uuid.uuid4().hex}.example", policy={"block_proxy": True}
    )
    provider = Provider(error=TimeoutError())
    monkeypatch.setattr("app.services.request_metadata.settings.trust_proxy_headers", True)
    monkeypatch.setattr("app.api.redirect.get_proxy_provider", lambda: provider)

    response = await client.get(
        "/CampaignA", headers={"Host": domain.name, "X-Real-IP": ip}, follow_redirects=False
    )

    assert response.headers["location"] == "https://allowed.example/landing"
    assert provider.calls == [(ip, 1.5)]
    assert await _reputation(ip) is None
    assert (await _latest_log(link.id)).result == "allowed"


@pytest.mark.asyncio
async def test_proxy_cache_write_failure_is_logged_without_changing_a_valid_redirect(client, monkeypatch, caplog):
    """A cache insert failure must not turn a valid provider response into a failed redirect."""
    domain, link = await _make_redirect_link(
        host=f"{uuid.uuid4().hex}.example", policy={"block_proxy": True}
    )
    provider = Provider(response={"is_proxy": False, "proxy_type": None})

    class FailingCacheSession:
        rolled_back = False

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def scalar(self, _statement):
            return None

        async def execute(self, _statement):
            return None

        async def commit(self):
            raise SQLAlchemyError("cache unavailable")

        async def rollback(self):
            self.rolled_back = True

    failing_cache = FailingCacheSession()
    monkeypatch.setattr("app.services.request_metadata.settings.trust_proxy_headers", True)
    monkeypatch.setattr("app.api.redirect.get_proxy_provider", lambda: provider)
    monkeypatch.setattr("app.services.proxy.AsyncSessionLocal", lambda: failing_cache)

    response = await client.get(
        "/CampaignA",
        headers={"Host": domain.name, "X-Real-IP": "203.0.113.213"},
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert response.headers["location"] == "https://allowed.example/landing"
    assert (await _latest_log(link.id)).result == "allowed"
    assert failing_cache.rolled_back is True
    assert "cache write failed" in caplog.text


@pytest.mark.asyncio
async def test_non_bot_proxy_policy_fails_open_when_maxmind_is_not_configured(client, monkeypatch):
    """Constructing a provider without credentials would make local redirects fail unexpectedly."""
    domain, link = await _make_redirect_link(
        host=f"{uuid.uuid4().hex}.example", policy={"block_proxy": True}
    )
    monkeypatch.setattr("app.services.proxy.settings.maxmind_account_id", None)
    monkeypatch.setattr("app.services.proxy.settings.maxmind_license_key", None)

    response = await client.get("/CampaignA", headers={"Host": domain.name}, follow_redirects=False)

    assert response.headers["location"] == "https://allowed.example/landing"
    assert (await _latest_log(link.id)).result == "allowed"


@pytest.mark.asyncio
async def test_short_code_is_case_sensitive_and_malformed_authorities_do_not_match(client, monkeypatch):
    """Case folding codes or accepting malformed authority could route a different link."""
    domain, _ = await _make_redirect_link(host="example.com")
    assert (
        await client.get("/campaigna", headers={"Host": domain.name}, follow_redirects=False)
    ).status_code == 404

    for authority in [
        "user@example.com",
        "example.com/path",
        "example.com:port",
        "example.com:65536",
        "[2001:db8::1",
        "2001:db8::1",
    ]:
        response = await client.get("/CampaignA", headers={"Host": authority}, follow_redirects=False)
        assert response.status_code == 404

    monkeypatch.setattr("app.services.request_metadata.settings.trust_proxy_headers", True)
    response = await client.get(
        "/CampaignA",
        headers={"Host": domain.name, "X-Forwarded-Host": "user@example.com"},
        follow_redirects=False,
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_normalized_dns_authority_selects_its_exact_domain_and_rejects_ip_literals(client):
    """Redirect tenant selection is limited to canonical ASCII DNS hostnames."""
    dns_domain, dns_link = await _make_redirect_link(host="canonical.example")

    dns = await client.get("/CampaignA", headers={"Host": "CANONICAL.EXAMPLE.:443"}, follow_redirects=False)
    ipv6 = await client.get("/CampaignA", headers={"Host": "[2001:db8::8]:8443"}, follow_redirects=False)

    assert dns.headers["location"] == "https://allowed.example/landing"
    assert ipv6.status_code == 404
    assert dns_domain.name == "canonical.example"
    assert (await _latest_log(dns_link.id)).request_url == "http://canonical.example:443/CampaignA"


@pytest.mark.asyncio
async def test_request_user_agent_is_parsed_once(client, monkeypatch):
    """Parsing twice per request wastes work and can split policy from its audit snapshot."""
    from app.services.request_metadata import parse_user_agent as real_parse_user_agent

    domain, _ = await _make_redirect_link(host=f"{uuid.uuid4().hex}.example")
    calls = 0

    def counting_parse_user_agent(*args, **kwargs):
        nonlocal calls
        calls += 1
        return real_parse_user_agent(*args, **kwargs)

    monkeypatch.setattr("app.services.request_metadata.parse_user_agent", counting_parse_user_agent)
    response = await client.get(
        "/CampaignA", headers={"Host": domain.name, "User-Agent": "Mozilla/5.0"}, follow_redirects=False
    )

    assert response.status_code == 302
    assert calls == 1


@pytest.mark.asyncio
async def test_log_writer_propagates_programming_errors():
    """Catching arbitrary exceptions would hide implementation bugs at the persistence boundary."""
    class BrokenSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        def add(self, _entry):
            raise RuntimeError("programming error")

    snapshot = AccessLogSnapshot(
        short_link_id=uuid.uuid4(), domain_id=uuid.uuid4(), target_url_id=None,
        request_url="https://example.com/CampaignA", domain_name="example.com", short_code="CampaignA",
        short_link_note=None, target_url=None, result="allowed", block_reason=None, block_detail=None,
        ip=None, country_code=None, referer=None, ua_raw=None, ua_browser=None, ua_browser_version=None,
        ua_os=None, ua_os_version=None, ua_device_type=None, ua_device_brand=None, ua_device_model=None,
        ua_bot_name=None, accessed_at=datetime.now(timezone.utc),
    )

    with pytest.raises(RuntimeError, match="programming error"):
        await write_access_log(lambda: BrokenSession(), snapshot)
