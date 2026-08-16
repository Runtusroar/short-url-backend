from datetime import date
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import get_current_user
from app.features.access_logs.schemas import (
    AccessLogFilterParams,
    AccessLogItemResponse,
    AccessLogPageResponse,
    AccessLogResponse,
    DailyStatsResponse,
)
from app.features.access_logs.service import (
    daily_stats as daily_stats_query,
    daily_summary as daily_summary_query,
    list_logs as list_logs_query,
)
from app.features.domains.dependencies import require_domain_access
from app.models import Domain, User
from app.models.enums import RedirectResult

router = APIRouter(prefix="/api/logs", tags=["logs"])


@router.get("", response_model=AccessLogPageResponse)
async def list_logs(
    short_link_id: UUID | None = None,
    short_code: str | None = Query(None),
    name: str | None = Query(None),
    date_from: date | None = None,
    date_to: date | None = None,
    country: list[str] = Query(default=[]),
    result: list[RedirectResult] = Query(default=[]),
    limit: int = Query(50, ge=1, le=200),
    cursor: str | None = Query(None),
    domain_id: UUID | None = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_domain: Domain = Depends(require_domain_access),
):
    filters = AccessLogFilterParams(
        short_link_id=short_link_id,
        short_code=short_code,
        name=name,
        date_from=date_from,
        date_to=date_to,
        countries=country,
        results=result,
    ).to_filters()
    page = await list_logs_query(
        db,
        current_user,
        current_domain,
        domain_id,
        filters,
        limit,
        cursor,
    )
    return AccessLogPageResponse(
        items=[
            AccessLogItemResponse(
                **AccessLogResponse.model_validate(row.log).model_dump(),
                short_code=row.short_code,
                short_link_name=row.short_link_name,
            )
            for row in page.items
        ],
        next_cursor=page.next_cursor,
        has_more=page.has_more,
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
