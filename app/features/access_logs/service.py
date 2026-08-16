from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import distinct, exists, func, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import NotFoundError, PermissionDeniedError
from app.features.access_logs.cursor import LogCursor, decode_cursor, encode_cursor
from app.features.access_logs.query import AccessLogFilters
from app.models import AccessLog, Domain, ShortLink, ShortLinkPermission, User
from app.models.enums import UserRole


@dataclass(frozen=True)
class AccessLogListRow:
    log: AccessLog
    short_code: str
    short_link_name: str


@dataclass(frozen=True)
class AccessLogPage:
    items: tuple[AccessLogListRow, ...]
    next_cursor: str | None
    has_more: bool


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


async def _can_view_link_in_domain(
    db: AsyncSession,
    user: User,
    link_id: UUID,
    domain_id: UUID,
) -> bool:
    link = await db.execute(
        select(ShortLink.id).where(
            ShortLink.id == link_id,
            ShortLink.domain_id == domain_id,
        )
    )
    if link.scalar_one_or_none() is None:
        return False
    return await can_view_link(db, user, link_id)


async def list_logs(
    db: AsyncSession,
    current_user: User,
    current_domain: Domain,
    domain_id: UUID | None,
    filters: AccessLogFilters,
    limit: int,
    cursor: str | None,
) -> AccessLogPage:
    if (
        domain_id
        and current_user.role != UserRole.ADMIN
        and domain_id != current_domain.id
    ):
        raise PermissionDeniedError()
    effective_domain = await resolve_effective_domain(
        domain_id, current_user, current_domain, db
    )
    if filters.short_link_id:
        if not await _can_view_link_in_domain(
            db,
            current_user,
            filters.short_link_id,
            effective_domain.id,
        ):
            raise PermissionDeniedError()

    filter_digest = filters.digest_scope(effective_domain.id, current_user)
    decoded_cursor = (
        decode_cursor(cursor, filter_digest, settings.secret_key)
        if cursor is not None
        else None
    )
    statement = (
        select(AccessLog, ShortLink.short_code, ShortLink.name)
        .join(ShortLink, ShortLink.id == AccessLog.short_link_id)
        .where(
            AccessLog.domain_id == effective_domain.id,
            ShortLink.domain_id == effective_domain.id,
        )
    )

    if current_user.role == UserRole.OPERATOR:
        statement = statement.where(ShortLink.owner_id == current_user.id)
    elif current_user.role == UserRole.CLIENT:
        statement = statement.where(
            exists().where(
                ShortLinkPermission.short_link_id == ShortLink.id,
                ShortLinkPermission.user_id == current_user.id,
            )
        )

    if filters.short_link_id:
        statement = statement.where(AccessLog.short_link_id == filters.short_link_id)
    if filters.short_code:
        statement = statement.where(
            ShortLink.short_code.startswith(filters.short_code, autoescape=True)
        )
    if filters.name:
        statement = statement.where(
            func.lower(ShortLink.name).contains(filters.name.lower(), autoescape=True)
        )
    if filters.date_from:
        statement = statement.where(AccessLog.access_date >= filters.date_from)
    if filters.date_to:
        statement = statement.where(AccessLog.access_date <= filters.date_to)
    if filters.countries:
        statement = statement.where(AccessLog.country.in_(filters.countries))
    if filters.results:
        statement = statement.where(
            AccessLog.result.in_(tuple(value.value for value in filters.results))
        )
    if decoded_cursor:
        statement = statement.where(
            tuple_(AccessLog.accessed_at, AccessLog.id)
            < tuple_(decoded_cursor.accessed_at, decoded_cursor.id)
        )

    result = await db.execute(
        statement.order_by(AccessLog.accessed_at.desc(), AccessLog.id.desc()).limit(
            limit + 1
        )
    )
    selected = result.all()
    has_more = len(selected) > limit
    page_rows = selected[:limit]
    items = tuple(
        AccessLogListRow(
            log=row[0],
            short_code=row[1],
            short_link_name=row[2],
        )
        for row in page_rows
    )
    next_cursor = None
    if has_more:
        last = items[-1].log
        next_cursor = encode_cursor(
            LogCursor(accessed_at=last.accessed_at.astimezone(UTC), id=last.id),
            filter_digest,
            settings.secret_key,
        )
    return AccessLogPage(items=items, next_cursor=next_cursor, has_more=has_more)


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
            AccessLog.access_date.label("date"),
            func.count().label("total"),
            func.count().filter(AccessLog.result == "allowed").label("allowed"),
            func.count().filter(AccessLog.result.in_(["denied", "blocked"])).label("denied"),
            func.count(distinct(AccessLog.client_ip)).label("unique_ips"),
        )
        .where(
            AccessLog.short_link_id == short_link_id,
            AccessLog.domain_id == effective_domain.id,
        )
        .group_by(AccessLog.access_date)
        .order_by(AccessLog.access_date.desc())
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
    start_date = datetime.now(ZoneInfo(effective_domain.timezone)).date() - timedelta(
        days=days - 1
    )

    result = await db.execute(
        select(
            AccessLog.access_date.label("date"),
            func.count().label("total"),
            func.count().filter(AccessLog.result == "allowed").label("allowed"),
            func.count().filter(AccessLog.result.in_(["denied", "blocked"])).label("denied"),
            func.count(distinct(AccessLog.client_ip)).label("unique_ips"),
        )
        .where(
            AccessLog.domain_id == effective_domain.id,
            AccessLog.access_date >= start_date,
        )
        .group_by(AccessLog.access_date)
        .order_by(AccessLog.access_date.asc())
    )
    return result.all()
