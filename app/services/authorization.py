from sqlalchemy import select
from sqlalchemy.sql import Select

from app.core.errors import NotFoundError, PermissionDeniedError
from app.db.models import AccessLevel, Domain, User, UserDomainAccess, UserRole


async def ensure_domain_access(
    db,
    user: User,
    domain_id,
    required: AccessLevel,
) -> Domain:
    if user.role == UserRole.ADMIN:
        domain = await db.scalar(
            select(Domain).where(Domain.id == domain_id, Domain.is_active.is_(True))
        )
        if domain is None:
            raise NotFoundError("域名")
        return domain

    grants = [
        UserDomainAccess.user_id == user.id,
        UserDomainAccess.domain_id == domain_id,
        Domain.is_active.is_(True),
    ]
    if required == AccessLevel.MANAGE:
        grants.append(UserDomainAccess.access_level == AccessLevel.MANAGE)
    domain = await db.scalar(
        select(Domain).join(UserDomainAccess).where(*grants)
    )
    if domain is None:
        raise PermissionDeniedError("无权访问该域名")
    return domain


def authorized_domain_ids_query(user: User) -> Select:
    if user.role == UserRole.ADMIN:
        return select(Domain.id).where(Domain.is_active.is_(True))
    return (
        select(UserDomainAccess.domain_id)
        .join(Domain, Domain.id == UserDomainAccess.domain_id)
        .where(
            UserDomainAccess.user_id == user.id,
            Domain.is_active.is_(True),
        )
    )


async def ensure_authorized_read_domain(db, user: User, domain_id) -> Domain:
    """Resolve a readable domain without exposing domain existence to subaccounts."""
    if user.role == UserRole.ADMIN:
        domain = await db.scalar(select(Domain).where(Domain.id == domain_id))
        if domain is None:
            raise NotFoundError("域名")
        return domain
    domain = await db.scalar(
        select(Domain).where(
            Domain.id == domain_id,
            Domain.id.in_(authorized_domain_ids_query(user)),
        )
    )
    if domain is None:
        raise PermissionDeniedError("无权访问该域名")
    return domain
