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
from app.core.errors import NotFoundError
from app.services.access import AccessContext, decide_access, target_error_decision
from app.services.access_log import AccessLogSnapshot, write_access_log
from app.services.redirect import choose_target
from app.services.request_metadata import RequestMetadata, extract_request_metadata, request_host
from app.services.proxy import get_proxy_provider

router = APIRouter(tags=["redirect"])


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

    decision = await decide_access(
        link.policy or SimpleNamespace(),
        AccessContext(
            ip=metadata.ip or "unknown",
            country=metadata.country,
            ua=metadata.ua,
            referer=metadata.referer,
        ),
        blacklisted=await _blacklist_entry(db, metadata.ip) or False,
        provider=get_proxy_provider(),
        db=db,
        now=datetime.now(timezone.utc),
    )
    target = choose_target(link.target_urls, decision.result)
    if target is None:
        target_kind = "允许" if decision.result == AccessResult.ALLOWED else "阻止"
        decision = target_error_decision(target_kind)

    await write_access_log(
        AsyncSessionLocal,
        _snapshot(domain=domain, link=link, metadata=metadata, decision=decision, target=target),
    )

    if target is None:
        raise NotFoundError("目标 URL")
    return Response(content=b"", status_code=302, headers={"Location": target.url})


@router.head("/{short_code}", include_in_schema=False)
async def redirect_head(short_code: str, request: Request, db: AsyncSession = Depends(get_db)) -> Response:
    return await redirect(short_code, request, db)
