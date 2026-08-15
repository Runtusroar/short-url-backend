from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError, NotFoundError
from app.features.blacklist.schemas import IpBlacklistCreate
from app.models import IpBlacklist, User


async def list_blacklist(db: AsyncSession) -> list[IpBlacklist]:
    result = await db.execute(select(IpBlacklist).order_by(IpBlacklist.created_at.desc()))
    return result.scalars().all()


async def add_to_blacklist(
    db: AsyncSession, payload: IpBlacklistCreate, current_user: User
) -> IpBlacklist:
    existing = await db.execute(select(IpBlacklist).where(IpBlacklist.ip == payload.ip))
    if existing.scalar_one_or_none():
        raise ConflictError("该 IP 已在黑名单中")

    entry = IpBlacklist(
        ip=payload.ip,
        reason=payload.reason,
        created_by=current_user.id,
    )
    db.add(entry)
    await db.commit()
    await db.refresh(entry)
    return entry


async def remove_from_blacklist(db: AsyncSession, entry_id: UUID) -> None:
    result = await db.execute(select(IpBlacklist).where(IpBlacklist.id == entry_id))
    entry = result.scalar_one_or_none()
    if not entry:
        raise NotFoundError("黑名单记录")
    await db.delete(entry)
    await db.commit()
