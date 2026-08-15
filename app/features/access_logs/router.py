from datetime import date
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import get_current_user
from app.features.access_logs.schemas import AccessLogResponse, DailyStatsResponse
from app.features.access_logs.service import (
    daily_stats as daily_stats_query,
    daily_summary as daily_summary_query,
    list_logs as list_logs_query,
)
from app.features.domains.dependencies import require_domain_access
from app.models import Domain, User

router = APIRouter(prefix="/api/logs", tags=["logs"])


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
    return await list_logs_query(
        db,
        current_user,
        current_domain,
        short_link_id,
        domain_id,
        date_from,
        date_to,
        limit,
        offset,
    )


@router.get("/daily", response_model=list[DailyStatsResponse])
async def daily_stats(
    short_link_id: UUID,
    domain_id: UUID | None = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_domain: Domain = Depends(require_domain_access),
):
    rows = await daily_stats_query(
        db, current_user, current_domain, short_link_id, domain_id
    )
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
    rows = await daily_summary_query(db, current_user, current_domain, days, domain_id)
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
