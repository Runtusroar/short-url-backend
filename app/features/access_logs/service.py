from datetime import date, timedelta
from uuid import UUID

from sqlalchemy import distinct, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError, PermissionDeniedError
from app.models import AccessLog, Domain, ShortLink, ShortLinkPermission, User


async def resolve_effective_domain(
    domain_id: UUID | None,
    current_user: User,
    current_domain: Domain,
    db: AsyncSession,
) -> Domain:
    if domain_id and current_user.role == "admin":
        result = await db.execute(
            select(Domain).where(Domain.id == domain_id, Domain.is_active == True)
        )
        domain = result.scalar_one_or_none()
        if not domain:
            raise NotFoundError("域名")
        return domain
    return current_domain


async def can_view_link(db: AsyncSession, user: User, link_id: UUID) -> bool:
    if user.role == "admin":
        return True
    link = await db.execute(select(ShortLink).where(ShortLink.id == link_id))
    link = link.scalar_one_or_none()
    if not link:
        return False
    if user.role == "operator" and str(link.owner_id) == str(user.id):
        return True
    if user.role == "client":
        perm = await db.execute(
            select(ShortLinkPermission).where(
                ShortLinkPermission.short_link_id == link_id,
                ShortLinkPermission.user_id == user.id,
            )
        )
        return perm.scalar_one_or_none() is not None
    return False


async def list_logs(
    db: AsyncSession,
    current_user: User,
    current_domain: Domain,
    short_link_id: UUID | None,
    domain_id: UUID | None,
    date_from: date | None,
    date_to: date | None,
    limit: int,
    offset: int,
) -> list[AccessLog]:
    effective_domain = await resolve_effective_domain(
        domain_id, current_user, current_domain, db
    )
    query = select(AccessLog).where(AccessLog.domain_id == effective_domain.id)

    if short_link_id:
        if not await can_view_link(db, current_user, short_link_id):
            raise PermissionDeniedError()
        query = query.where(AccessLog.short_link_id == short_link_id)

    if date_from:
        query = query.where(AccessLog.accessed_at_plus8 >= date_from)
    if date_to:
        query = query.where(AccessLog.accessed_at_plus8 <= date_to)

    query = query.order_by(AccessLog.accessed_at.desc()).offset(offset).limit(limit)
    result = await db.execute(query)
    return result.scalars().all()


async def daily_stats(
    db: AsyncSession,
    current_user: User,
    current_domain: Domain,
    short_link_id: UUID,
    domain_id: UUID | None,
):
    effective_domain = await resolve_effective_domain(
        domain_id, current_user, current_domain, db
    )
    if not await can_view_link(db, current_user, short_link_id):
        raise PermissionDeniedError()

    result = await db.execute(
        select(
            AccessLog.accessed_at_plus8.label("date"),
            func.count().label("total"),
            func.count().filter(AccessLog.result == "allowed").label("allowed"),
            func.count().filter(AccessLog.result.in_(["denied", "blocked"])).label("denied"),
            func.count(distinct(AccessLog.ip)).label("unique_ips"),
        )
        .where(
            AccessLog.short_link_id == short_link_id,
            AccessLog.domain_id == effective_domain.id,
        )
        .group_by(AccessLog.accessed_at_plus8)
        .order_by(AccessLog.accessed_at_plus8.desc())
    )
    return result.all()


async def daily_summary(
    db: AsyncSession,
    current_user: User,
    current_domain: Domain,
    days: int,
    domain_id: UUID | None,
):
    effective_domain = await resolve_effective_domain(
        domain_id, current_user, current_domain, db
    )
    start_date = date.today() - timedelta(days=days - 1)

    result = await db.execute(
        select(
            AccessLog.accessed_at_plus8.label("date"),
            func.count().label("total"),
            func.count().filter(AccessLog.result == "allowed").label("allowed"),
            func.count().filter(AccessLog.result.in_(["denied", "blocked"])).label("denied"),
            func.count(distinct(AccessLog.ip)).label("unique_ips"),
        )
        .where(
            AccessLog.domain_id == effective_domain.id,
            AccessLog.accessed_at_plus8 >= start_date,
        )
        .group_by(AccessLog.accessed_at_plus8)
        .order_by(AccessLog.accessed_at_plus8.asc())
    )
    return result.all()
