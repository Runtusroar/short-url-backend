from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.domains import _get_host
from app.exceptions import NotFoundError, PermissionDeniedError
from app.models import AccessLog, Domain, IpBlacklist, ShortLink, TargetUrl
from app.services.geoip import get_country
from app.services.redirect import get_redirect_target
from app.services.ua import get_platform

router = APIRouter(tags=["redirect"])


def _get_client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _is_proxy(request: Request) -> bool:
    """Detect proxy/VPN by common proxy headers."""
    proxy_headers = ["x-forwarded-for", "x-real-ip", "via", "forwarded"]
    return any(request.headers.get(header) for header in proxy_headers)


async def _is_blacklisted(db: AsyncSession, ip: str) -> bool:
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


async def _resolve_domain(db: AsyncSession, request: Request) -> Domain:
    host = _get_host(request)
    result = await db.execute(select(Domain).where(Domain.name == host))
    domain = result.scalar_one_or_none()
    if domain and domain.is_active:
        return domain
    result = await db.execute(select(Domain).where(Domain.is_default == True))
    domain = result.scalar_one_or_none()
    if domain and domain.is_active:
        return domain
    raise NotFoundError("域名")


@router.api_route("/{short_code}", methods=["GET", "HEAD"])
async def redirect(
    short_code: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    domain = await _resolve_domain(db, request)
    result = await db.execute(
        select(ShortLink).where(
            ShortLink.domain_id == domain.id,
            ShortLink.short_code == short_code,
            ShortLink.is_active == True,
        )
    )
    link = result.scalar_one_or_none()
    if not link:
        raise NotFoundError("短链")

    ip = _get_client_ip(request)
    is_blacklisted = await _is_blacklisted(db, ip)
    country = get_country(ip)
    ua_string = request.headers.get("user-agent")
    platform = get_platform(ua_string)
    referer = request.headers.get("referer")
    is_proxy = _is_proxy(request)

    action, target = await get_redirect_target(db, link, country, platform, referer, is_blacklisted, is_proxy)

    await _log_access(db, link, domain, target, action, ip, country, ua_string, platform, referer)

    if not target:
        if action in ("denied", "blocked"):
            raise PermissionDeniedError("访问被拒绝")
        raise NotFoundError("目标URL")

    return Response(status_code=302, headers={"Location": target.url})
