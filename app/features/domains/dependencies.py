from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.exceptions import NotFoundError, PermissionDeniedError
from app.core.security import get_current_user
from app.models import Domain, User, UserDomain


def get_request_host(request: Request) -> str:
    host = request.headers.get("host", "localhost")
    return host.split(":")[0].lower()


async def get_current_domain(
    request: Request, db: AsyncSession = Depends(get_db)
) -> Domain:
    host = get_request_host(request)
    result = await db.execute(
        select(Domain).where(Domain.name == host, Domain.is_active.is_(True))
    )
    domain = result.scalar_one_or_none()
    if domain:
        return domain
    result = await db.execute(
        select(Domain).where(Domain.is_default.is_(True), Domain.is_active.is_(True))
    )
    domain = result.scalar_one_or_none()
    if domain:
        return domain
    raise NotFoundError("域名")


async def has_domain_access(user: User, domain_id, db: AsyncSession) -> bool:
    if user.role == "admin":
        return True
    result = await db.execute(
        select(UserDomain).where(
            UserDomain.user_id == user.id,
            UserDomain.domain_id == domain_id,
        )
    )
    return result.scalar_one_or_none() is not None


async def require_domain_access(
    current_user: User = Depends(get_current_user),
    current_domain: Domain = Depends(get_current_domain),
    db: AsyncSession = Depends(get_db),
):
    if not await has_domain_access(current_user, current_domain.id, db):
        raise PermissionDeniedError("无权访问该域名")
    return current_domain
