"""Authorized, cursor-paginated access-log investigation endpoint."""

from __future__ import annotations

from datetime import datetime, time, timezone
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Query
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import APIError, ErrorCode
from app.db.session import get_db
from app.db.models import AccessLog, AccessResult, BlockReason, User
from app.dependencies import get_current_user
from app.schemas.log import AccessLogCursorPage, AccessLogResponse
from app.services.authorization import ensure_authorized_read_domain
from app.services.cursor import CursorError, decode_cursor, encode_cursor

router = APIRouter(prefix="/api/access-logs", tags=["access-logs"])


def _time_boundary(value: str | None, *, end: bool) -> datetime | None:
    """Interpret bare values in the configured application timezone, then query in UTC."""
    if value is None:
        return None
    try:
        if len(value) == 10:
            parsed = datetime.combine(datetime.fromisoformat(value).date(), time.max if end else time.min)
        else:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise APIError(ErrorCode.VALIDATION_ERROR, "时间范围格式不正确", 422) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        zone = ZoneInfo(settings.app_timezone)
        candidates = []
        for fold in (0, 1):
            candidate = parsed.replace(tzinfo=zone, fold=fold)
            restored = candidate.astimezone(timezone.utc).astimezone(zone).replace(tzinfo=None)
            if restored == parsed:
                candidates.append(candidate)
        offsets = {candidate.utcoffset() for candidate in candidates}
        if len(offsets) != 1:
            raise APIError(ErrorCode.VALIDATION_ERROR, "歧义时间必须指定 UTC 偏移量", 422)
        parsed = candidates[0]
    return parsed.astimezone(timezone.utc)


def _response(row: AccessLog) -> AccessLogResponse:
    return AccessLogResponse(
        id=row.id,
        short_link_id=row.short_link_id,
        domain_id=row.domain_id,
        target_url_id=row.target_url_id,
        request_url=row.request_url,
        domain_name=row.domain_name,
        short_code=row.short_code,
        short_link_note=row.short_link_note,
        target_url=row.target_url,
        result=row.result,
        block_reason=row.block_reason,
        block_detail=row.block_detail,
        ip=str(row.ip) if row.ip is not None else None,
        country_code=row.country_code,
        referer=row.referer,
        ua_raw=row.ua_raw,
        ua_browser=row.ua_browser,
        ua_browser_version=row.ua_browser_version,
        ua_os=row.ua_os,
        ua_os_version=row.ua_os_version,
        ua_device_type=row.ua_device_type,
        ua_device_brand=row.ua_device_brand,
        ua_device_model=row.ua_device_model,
        ua_bot_name=row.ua_bot_name,
        accessed_at=row.accessed_at,
    )


@router.get("", response_model=AccessLogCursorPage)
async def list_access_logs(
    domain_id: UUID,
    short_link_id: UUID | None = None,
    keyword: str | None = Query(default=None, max_length=4000),
    date_from: str | None = None,
    date_to: str | None = None,
    country: str | None = Query(default=None, pattern=r"^[A-Za-z]{2}$"),
    result: AccessResult | None = None,
    reason: BlockReason | None = None,
    cursor: str | None = None,
    page_size: int = Query(default=50, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # Authorization deliberately precedes every user-controlled filtering predicate.
    await ensure_authorized_read_domain(db, current_user, domain_id)
    filters = [AccessLog.domain_id == domain_id]
    if short_link_id is not None:
        filters.append(AccessLog.short_link_id == short_link_id)
    if keyword and (term := keyword.strip()):
        escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        pattern = f"%{escaped}%"
        filters.append(
            or_(
                AccessLog.short_code.ilike(pattern, escape="\\"),
                AccessLog.short_link_note.ilike(pattern, escape="\\"),
            )
        )
    if (start := _time_boundary(date_from, end=False)) is not None:
        filters.append(AccessLog.accessed_at >= start)
    if (finish := _time_boundary(date_to, end=True)) is not None:
        filters.append(AccessLog.accessed_at <= finish)
    if start is not None and finish is not None and start > finish:
        raise APIError(ErrorCode.VALIDATION_ERROR, "开始时间不能晚于结束时间", 422)
    if country:
        filters.append(AccessLog.country_code == country.upper())
    if result is not None:
        filters.append(AccessLog.result == result)
    if reason is not None:
        filters.append(AccessLog.block_reason == reason)
    if cursor:
        try:
            cursor_at, cursor_id = decode_cursor(cursor)
        except CursorError as exc:
            raise APIError(ErrorCode.INVALID_CURSOR, "游标无效", 422) from exc
        filters.append(
            or_(
                AccessLog.accessed_at < cursor_at,
                and_(AccessLog.accessed_at == cursor_at, AccessLog.id < cursor_id),
            )
        )

    rows = list(
        (
            await db.execute(
                select(AccessLog)
                .where(*filters)
                .order_by(AccessLog.accessed_at.desc(), AccessLog.id.desc())
                .limit(page_size + 1)
            )
        ).scalars()
    )
    has_next = len(rows) > page_size
    page_rows = rows[:page_size]
    return AccessLogCursorPage(
        items=[_response(row) for row in page_rows],
        next_cursor=encode_cursor(page_rows[-1].accessed_at, page_rows[-1].id)
        if has_next and page_rows
        else None,
    )
