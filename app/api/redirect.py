"""Public short-code redirect endpoints."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import cast, select
from sqlalchemy.dialects.postgresql import INET
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.session import AsyncSessionLocal, get_db
from app.db.models import AccessResult, Domain, IpBlacklist, ShortLink
from app.core.errors import NotFoundError, TargetUnavailableError
from app.services.access import AccessContext, decide_access, target_error_decision
from app.services.access_log import AccessLogSnapshot, write_access_log
from app.services.redirect import choose_target
from app.services.request_metadata import RequestMetadata, extract_request_metadata, request_host
from app.services.proxy import get_proxy_provider

router = APIRouter(tags=["redirect"])

_POLICY_FIELDS = (
    "country_mode",
    "countries",
    "platform_mode",
    "platforms",
    "referer_mode",
    "referer_patterns",
    "block_proxy",
    "block_bot",
)


async def _resolve_domain(db: AsyncSession, request: Request) -> Domain:
    host = request_host(request)
    if host is None:
        raise NotFoundError("域名")
    domain = await db.scalar(
        select(Domain).where(
            Domain.name == host,
            Domain.is_active.is_(True),
        )
    )
    if domain is None:
        raise NotFoundError("域名")
    return domain


async def _blacklist_entry(db: AsyncSession, ip: str | None) -> IpBlacklist | None:
    if ip is None:
        return None
    return await db.scalar(select(IpBlacklist).where(IpBlacklist.ip == cast(ip, INET)))


def _snapshot(
    *,
    domain: Domain,
    link: ShortLink,
    metadata: RequestMetadata,
    decision,
    target,
) -> AccessLogSnapshot:
    ua = metadata.ua
    return AccessLogSnapshot(
        short_link_id=link.id,
        domain_id=domain.id,
        target_url_id=target.id if target else None,
        request_url=metadata.request_url,
        domain_name=domain.name,
        short_code=link.short_code,
        short_link_note=link.note,
        target_url=target.url if target else None,
        result=str(decision.result),
        block_reason=str(decision.block_reason) if decision.block_reason else None,
        block_detail=decision.block_detail,
        ip=metadata.ip,
        country_code=metadata.country,
        referer=metadata.referer,
        ua_raw=ua.raw,
        ua_browser=ua.browser,
        ua_browser_version=ua.browser_version,
        ua_os=ua.os,
        ua_os_version=ua.os_version,
        ua_device_type=ua.device_type,
        ua_device_brand=ua.brand,
        ua_device_model=ua.model,
        ua_bot_name=ua.bot_name,
        accessed_at=datetime.now(timezone.utc),
    )


def _policy_snapshot(policy) -> SimpleNamespace:
    """Detach policy values before a paid lookup releases the request transaction."""
    if policy is None:
        return SimpleNamespace()
    return SimpleNamespace(**{field: getattr(policy, field) for field in _POLICY_FIELDS})


@router.get("/{short_code}")
async def redirect(short_code: str, request: Request, db: AsyncSession = Depends(get_db)) -> Response:
    metadata = extract_request_metadata(request)
    domain = await _resolve_domain(db, request)
    link = await db.scalar(
        select(ShortLink)
        .where(
            ShortLink.domain_id == domain.id,
            ShortLink.short_code == short_code,
            ShortLink.is_active.is_(True),
        )
        .options(selectinload(ShortLink.target_urls), selectinload(ShortLink.policy))
    )
    if link is None:
        raise NotFoundError("短链")

    blacklisted = await _blacklist_entry(db, metadata.ip)
    domain_snapshot = SimpleNamespace(id=domain.id, name=domain.name)
    link_snapshot = SimpleNamespace(id=link.id, short_code=link.short_code, note=link.note)
    target_snapshots = [
        SimpleNamespace(
            id=target.id,
            url=target.url,
            url_type=target.url_type,
            weight=target.weight,
            is_active=target.is_active,
        )
        for target in link.target_urls
    ]
    policy_snapshot = _policy_snapshot(link.policy)
    blacklist_snapshot = blacklisted.reason if blacklisted and blacklisted.reason else bool(blacklisted)

    # All route-owned reads are complete.  Do not keep this transaction open
    # while MaxMind waits on the network; its cache opens its own short-lived
    # sessions before and after the provider call.
    await db.rollback()

    decision = await decide_access(
        policy_snapshot,
        AccessContext(
            ip=metadata.ip,
            country=metadata.country,
            ua=metadata.ua,
            referer=metadata.referer,
        ),
        blacklisted=blacklist_snapshot,
        provider=get_proxy_provider(),
        now=datetime.now(timezone.utc),
    )
    target = choose_target(target_snapshots, decision.result)
    if target is None:
        target_type = "allowed" if decision.result == AccessResult.ALLOWED else "blocked"
        target_kind = "允许" if target_type == "allowed" else "阻止"
        decision = target_error_decision(target_kind)

    await write_access_log(
        AsyncSessionLocal,
        _snapshot(domain=domain_snapshot, link=link_snapshot, metadata=metadata, decision=decision, target=target),
    )

    if target is None:
        raise TargetUnavailableError(target_type)
    return Response(content=b"", status_code=302, headers={"Location": target.url})


@router.head("/{short_code}", include_in_schema=False)
async def redirect_head(short_code: str, request: Request, db: AsyncSession = Depends(get_db)) -> Response:
    return await redirect(short_code, request, db)
