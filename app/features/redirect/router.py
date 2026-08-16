from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.client_ip import get_client_ip
from app.core.database import get_db
from app.features.domains.dependencies import get_request_host
from app.features.redirect.service import execute_redirect

router = APIRouter(tags=["redirect"])


@router.api_route("/{short_code}", methods=["GET", "HEAD"])
async def redirect(
    short_code: str,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    host = get_request_host(request)
    client_ip = get_client_ip(request)
    ua_string = request.headers.get("user-agent")
    referer = request.headers.get("referer")
    target_url = await execute_redirect(
        db,
        host,
        short_code,
        client_ip,
        ua_string,
        referer,
        request.method,
    )

    return Response(status_code=302, headers={"Location": target_url})
