from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_staff
from app.database import get_db
from app.exceptions import ConflictError, NotFoundError
from app.models import IpBlacklist, User
from app.schemas import IpBlacklistCreate, IpBlacklistResponse

router = APIRouter(prefix="/api/ip-blacklist", tags=["ip-blacklist"])


@router.get("", response_model=list[IpBlacklistResponse])
async def list_blacklist(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_staff),
):
    result = await db.execute(select(IpBlacklist).order_by(IpBlacklist.created_at.desc()))
    return result.scalars().all()


@router.post("", response_model=IpBlacklistResponse)
async def add_to_blacklist(
    payload: IpBlacklistCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_staff),
):
    existing = await db.execute(select(IpBlacklist).where(IpBlacklist.ip == payload.ip))
    if existing.scalar_one_or_none():
        raise ConflictError("该 IP 已在黑名单中")

    entry = IpBlacklist(ip=payload.ip, reason=payload.reason, created_by=current_user.id)
    db.add(entry)
    await db.commit()
    await db.refresh(entry)
    return entry


@router.delete("/{entry_id}")
async def remove_from_blacklist(
    entry_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_staff),
):
    result = await db.execute(select(IpBlacklist).where(IpBlacklist.id == entry_id))
    entry = result.scalar_one_or_none()
    if not entry:
        raise NotFoundError("黑名单记录")
    await db.delete(entry)
    await db.commit()
    return {"detail": "Removed"}
