import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.db import AsyncSessionLocal
from app.db.models import AccessLog, Domain, IpBlacklist, LinkPolicy, ShortLink, TargetUrl


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
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        def add(self, _entry):
            return None

        async def commit(self):
            raise SQLAlchemyError("database unavailable")

        async def rollback(self):
            return None

    monkeypatch.setattr("app.api.redirect.AsyncSessionLocal", lambda: FailingSession())

    response = await client.get("/CampaignA", headers={"Host": domain.name}, follow_redirects=False)

    assert response.status_code == 302
    assert response.headers["location"] == "https://allowed.example/landing"
