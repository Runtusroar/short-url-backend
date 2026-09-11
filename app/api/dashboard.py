"""Authorized, bounded dashboard aggregates."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Query
from sqlalchemy import String, cast, distinct, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import ErrorCode
from app.database import get_db
from app.db.models import AccessLog, User
from app.dependencies import get_current_user
from app.exceptions import APIError
from app.schemas.dashboard import (
    DashboardDailyBucket,
    DashboardResponse,
    DashboardTopLink,
    DashboardTopReferer,
)
from app.services.authorization import ensure_authorized_read_domain

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


def _range(days: int) -> tuple[date, datetime, datetime]:
    local_zone = ZoneInfo(settings.app_timezone)
    today = datetime.now(timezone.utc).astimezone(local_zone).date()
    first_day = today - timedelta(days=days - 1)
    start = datetime.combine(first_day, time.min, tzinfo=local_zone).astimezone(timezone.utc)
    end = datetime.combine(today + timedelta(days=1), time.min, tzinfo=local_zone).astimezone(timezone.utc)
    return first_day, start, end


@router.get("", response_model=DashboardResponse)
async def dashboard(
    domain_id: UUID,
    days: int = Query(default=7),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if days not in {7, 30}:
        raise APIError(ErrorCode.VALIDATION_ERROR, "days 仅支持 7 或 30", 422)
    # Resolve the requested domain before executing any aggregate query.
    await ensure_authorized_read_domain(db, current_user, domain_id)
    first_day, start, end = _range(days)
    filters = (AccessLog.domain_id == domain_id, AccessLog.accessed_at >= start, AccessLog.accessed_at < end)
    totals = (
        await db.execute(
            select(
                func.count(AccessLog.id).label("total"),
                func.count(AccessLog.id).filter(AccessLog.result == "allowed").label("allowed"),
                func.count(AccessLog.id).filter(AccessLog.result == "blocked").label("blocked"),
                func.count(distinct(AccessLog.ip)).label("unique_ips"),
            ).where(*filters)
        )
    ).one()

    local_day = func.date(func.timezone(settings.app_timezone, AccessLog.accessed_at))
    daily_rows = (
        await db.execute(
            select(
                local_day.label("day"),
                func.count(AccessLog.id).label("total"),
                func.count(AccessLog.id).filter(AccessLog.result == "allowed").label("allowed"),
                func.count(AccessLog.id).filter(AccessLog.result == "blocked").label("blocked"),
            )
            .where(*filters)
            .group_by(local_day)
            .order_by(local_day)
        )
    ).all()
    daily_by_day = {row.day: row for row in daily_rows}
    daily = []
    for offset in range(days):
        day = first_day + timedelta(days=offset)
        row = daily_by_day.get(day)
        daily.append(
            DashboardDailyBucket(
                date=day,
                total=row.total if row else 0,
                allowed=row.allowed if row else 0,
                blocked=row.blocked if row else 0,
            )
        )

    identity = func.coalesce(
        cast(AccessLog.short_link_id, String),
        AccessLog.short_code,
        cast(AccessLog.id, String),
    ).label("identity")
    count = func.count(AccessLog.id).label("total")
    top_identities = (
        select(identity, count)
        .where(*filters)
        .group_by(identity)
        .order_by(count.desc(), identity.asc())
        .limit(5)
        .subquery()
    )
    ranked_snapshots = (
        select(
            identity,
            AccessLog.short_code,
            AccessLog.short_link_note,
            func.row_number()
            .over(
                partition_by=identity,
                order_by=(AccessLog.accessed_at.desc(), AccessLog.id.desc()),
            )
            .label("snapshot_rank"),
        )
        .where(*filters)
        .subquery()
    )
    top_link_rows = (
        await db.execute(
            select(
                ranked_snapshots.c.short_code,
                ranked_snapshots.c.short_link_note,
                top_identities.c.total,
            )
            .join(
                ranked_snapshots,
                (ranked_snapshots.c.identity == top_identities.c.identity)
                & (ranked_snapshots.c.snapshot_rank == 1),
            )
            .order_by(top_identities.c.total.desc(), top_identities.c.identity.asc())
        )
    ).all()
    top_referer_rows = (
        await db.execute(
            select(AccessLog.referer, count)
            .where(*filters, AccessLog.referer.is_not(None), func.btrim(AccessLog.referer) != "")
            .group_by(AccessLog.referer)
            .order_by(count.desc(), AccessLog.referer.asc())
            .limit(5)
        )
    ).all()
    return DashboardResponse(
        total=totals.total or 0,
        allowed=totals.allowed or 0,
        blocked=totals.blocked or 0,
        unique_ips=totals.unique_ips or 0,
        daily=daily,
        top_links=[
            DashboardTopLink(short_code=row.short_code, short_link_note=row.short_link_note, total=row.total)
            for row in top_link_rows
        ],
        top_referers=[DashboardTopReferer(referer=row.referer, total=row.total) for row in top_referer_rows],
    )
