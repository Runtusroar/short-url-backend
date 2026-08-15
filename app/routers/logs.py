from datetime import date
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy import distinct, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains import require_domain_access
from app.core.database import get_db
from app.core.exceptions import NotFoundError, PermissionDeniedError
from app.core.security import get_current_user
from app.models import AccessLog, Domain, ShortLink, ShortLinkPermission, User
from app.schemas import AccessLogResponse, DailyStatsResponse

router = APIRouter(prefix="/api/logs", tags=["logs"])


async def _resolve_effective_domain(
    domain_id: UUID | None,
    current_user: User,
    current_domain: Domain,
    db: AsyncSession,
) -> Domain:
    if domain_id and current_user.role == "admin":
        result = await db.execute(select(Domain).where(Domain.id == domain_id, Domain.is_active == True))
        domain = result.scalar_one_or_none()
        if not domain:
            raise NotFoundError("域名")
        return domain
    return current_domain


async def _can_view_link(db: AsyncSession, user: User, link_id: UUID) -> bool:
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


@router.get("", response_model=list[AccessLogResponse])
async def list_logs(
    short_link_id: UUID | None = None,
    domain_id: UUID | None = Query(None),
    date_from: date | None = None,
    date_to: date | None = None,
    limit: int = 100,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_domain: Domain = Depends(require_domain_access),
):
    effective_domain = await _resolve_effective_domain(domain_id, current_user, current_domain, db)
    query = select(AccessLog).where(AccessLog.domain_id == effective_domain.id)

    if short_link_id:
        if not await _can_view_link(db, current_user, short_link_id):
            raise PermissionDeniedError()
        query = query.where(AccessLog.short_link_id == short_link_id)

    if date_from:
        query = query.where(AccessLog.accessed_at_plus8 >= date_from)
    if date_to:
        query = query.where(AccessLog.accessed_at_plus8 <= date_to)

    query = query.order_by(AccessLog.accessed_at.desc()).offset(offset).limit(limit)
    result = await db.execute(query)
    return result.scalars().all()


@router.get("/daily", response_model=list[DailyStatsResponse])
async def daily_stats(
    short_link_id: UUID,
    domain_id: UUID | None = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_domain: Domain = Depends(require_domain_access),
):
    effective_domain = await _resolve_effective_domain(domain_id, current_user, current_domain, db)
    if not await _can_view_link(db, current_user, short_link_id):
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
    rows = result.all()
    return [
        DailyStatsResponse(
            date=str(row.date),
            total=row.total,
            allowed=row.allowed,
            denied=row.denied,
            unique_ips=row.unique_ips,
        )
        for row in rows
    ]


@router.get("/daily-summary", response_model=list[DailyStatsResponse])
async def daily_summary(
    days: int = Query(7, ge=1, le=30),
    domain_id: UUID | None = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_domain: Domain = Depends(require_domain_access),
):
    """Return aggregated daily stats for the current domain."""
    from datetime import date, timedelta

    effective_domain = await _resolve_effective_domain(domain_id, current_user, current_domain, db)
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
    rows = result.all()
    return [
        DailyStatsResponse(
            date=str(row.date),
            total=row.total,
            allowed=row.allowed,
            denied=row.denied,
            unique_ips=row.unique_ips,
        )
        for row in rows
    ]
