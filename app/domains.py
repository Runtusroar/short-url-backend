from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.database import get_db
from app.models import Domain, User, UserDomain


def _get_host(request: Request) -> str:
    forwarded_host = request.headers.get("x-forwarded-host")
    if forwarded_host:
        return forwarded_host.split(",")[0].strip().lower()
    host = request.headers.get("host", "localhost")
    return host.split(":")[0].lower()


async def get_current_domain(
    request: Request, db: AsyncSession = Depends(get_db)
) -> Domain:
    host = _get_host(request)
    result = await db.execute(select(Domain).where(Domain.name == host))
    domain = result.scalar_one_or_none()
    if domain:
        return domain
    # Fallback to default domain
    result = await db.execute(select(Domain).where(Domain.is_default == True))
    domain = result.scalar_one_or_none()
    if domain:
        return domain
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Domain not found")


async def _has_domain_access(user: User, domain_id, db: AsyncSession) -> bool:
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
    if not await _has_domain_access(current_user, current_domain.id, db):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No access to this domain")
    return current_domain


async def require_domain_staff(
    current_user: User = Depends(get_current_user),
    current_domain: Domain = Depends(get_current_domain),
    db: AsyncSession = Depends(get_db),
):
    if current_user.role not in ("admin", "operator"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Permission denied")
    if not await _has_domain_access(current_user, current_domain.id, db):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No access to this domain")
    return current_domain
