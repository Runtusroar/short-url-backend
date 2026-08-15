from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.client_ip import get_client_ip
from app.core.database import get_db
from app.core.exceptions import NotFoundError, PermissionDeniedError
from app.features.domains.dependencies import get_request_host
from app.features.redirect.service import (
    is_blacklisted,
    resolve_domain_and_link,
    select_and_log_redirect,
)
from app.features.redirect.ua import get_platform
from app.integrations.maxmind.country import get_country

router = APIRouter(tags=["redirect"])


@router.api_route("/{short_code}", methods=["GET", "HEAD"])
async def redirect(
    short_code: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    host = get_request_host(request)
    domain, link = await resolve_domain_and_link(db, host, short_code)
    ip = get_client_ip(request)
    blacklisted = await is_blacklisted(db, ip)
    country = get_country(ip)
    ua_string = request.headers.get("user-agent")
    platform = get_platform(ua_string)
    referer = request.headers.get("referer")
    is_proxy = platform == "bot"

    action, target = await select_and_log_redirect(
        db,
        link,
        domain,
        ip,
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

    return Response(status_code=302, headers={"Location": target.url})
