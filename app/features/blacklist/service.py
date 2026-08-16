from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import cast, or_, select
from sqlalchemy.dialects.postgresql import INET
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError, NotFoundError
from app.features.blacklist.schemas import IpBlacklistCreate
from app.models import IpBlacklist, User

DEFAULT_REMOVAL_REASON = "Removed by staff"


def _is_active(entry: IpBlacklist, now: datetime) -> bool:
    return entry.removed_at is None and (
        entry.expires_at is None or entry.expires_at > now
    )


async def list_blacklist(db: AsyncSession) -> list[IpBlacklist]:
    now = datetime.now(UTC)
    result = await db.execute(
        select(IpBlacklist)
        .where(
            IpBlacklist.removed_at.is_(None),
            or_(IpBlacklist.expires_at.is_(None), IpBlacklist.expires_at > now),
        )
        .order_by(IpBlacklist.created_at.desc())
    )
    return result.scalars().all()


async def add_to_blacklist(
    db: AsyncSession, payload: IpBlacklistCreate, current_user: User
) -> IpBlacklist:
    canonical_ip = str(payload.ip)
    existing = (
        await db.execute(
            select(IpBlacklist)
            .where(IpBlacklist.ip == cast(canonical_ip, INET))
            .with_for_update()
        )
    ).scalar_one_or_none()
    now = datetime.now(UTC)
    if existing and _is_active(existing, now):
        await db.rollback()
        raise ConflictError("该 IP 已在黑名单中")

    if existing is None:
        entry = IpBlacklist(
            ip=canonical_ip,
            reason=payload.reason,
            expires_at=payload.expires_at,
            created_by=current_user.id,
            created_at=now,
        )
        db.add(entry)
    else:
        entry = existing
        entry.reason = payload.reason
        entry.expires_at = payload.expires_at
        entry.created_by = current_user.id
        entry.created_at = now
        entry.removed_at = None
        entry.removed_by = None
        entry.removal_reason = None

    try:
        await db.commit()
        await db.refresh(entry)
    except IntegrityError as exc:
        await db.rollback()
        raise ConflictError("该 IP 已在黑名单中") from exc
    return entry


async def remove_from_blacklist(
    db: AsyncSession, entry_id: UUID, current_user: User
) -> None:
    result = await db.execute(select(IpBlacklist).where(IpBlacklist.id == entry_id))
    entry = result.scalar_one_or_none()
    if not entry:
        raise NotFoundError("黑名单记录")
    if entry.removed_at is not None:
        return
    entry.removed_at = datetime.now(UTC)
    entry.removed_by = current_user.id
    entry.removal_reason = DEFAULT_REMOVAL_REASON
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise ConflictError("黑名单记录状态冲突") from exc
