from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy import cast, func, select
from sqlalchemy.dialects.postgresql import INET
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ErrorCode
from app.database import get_db
from app.db.models import IpBlacklist, User
from app.dependencies import require_admin
from app.exceptions import ConflictError, NotFoundError
from app.schemas.common import Page
from app.schemas.security import IpBlacklistCreate, IpBlacklistResponse, IpBlacklistUpdate


router = APIRouter(prefix="/api/security", tags=["security"])


def _entry_response(entry: IpBlacklist, creator_username: str | None) -> IpBlacklistResponse:
    return IpBlacklistResponse(
        id=entry.id,
        ip=str(entry.ip),
        reason=entry.reason,
        created_by=entry.created_by,
        created_by_username=creator_username,
        created_at=entry.created_at,
    )


async def _entry_with_creator(db: AsyncSession, entry_id: UUID) -> IpBlacklistResponse:
    result = await db.execute(
        select(IpBlacklist, User.username)
        .outerjoin(User, User.id == IpBlacklist.created_by)
        .where(IpBlacklist.id == entry_id)
    )
    entry, username = result.one()
    return _entry_response(entry, username)


@router.get("/ip-blacklist", response_model=Page[IpBlacklistResponse])
async def list_ip_blacklist(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    total = await db.scalar(select(func.count()).select_from(IpBlacklist)) or 0
    result = await db.execute(
        select(IpBlacklist, User.username)
        .outerjoin(User, User.id == IpBlacklist.created_by)
        .order_by(IpBlacklist.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    return Page(
        items=[_entry_response(entry, username) for entry, username in result],
        page=page,
        page_size=page_size,
        total=total,
    )


@router.post("/ip-blacklist", response_model=IpBlacklistResponse, status_code=status.HTTP_201_CREATED)
async def create_ip_blacklist_entry(
    payload: IpBlacklistCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    await db.commit()
    async with db.begin():
        existing = await db.scalar(
            select(IpBlacklist.id).where(IpBlacklist.ip == cast(payload.ip, INET))
        )
        if existing:
            raise ConflictError("该 IP 已在黑名单中", ErrorCode.IP_BLACKLIST_CONFLICT)
        entry = IpBlacklist(ip=payload.ip, reason=payload.reason, created_by=current_user.id)
        db.add(entry)
        await db.flush()
        response = _entry_response(entry, current_user.username)
    return response


@router.put("/ip-blacklist/{entry_id}", response_model=IpBlacklistResponse)
async def update_ip_blacklist_entry(
    entry_id: UUID,
    payload: IpBlacklistUpdate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    await db.commit()
    async with db.begin():
        entry = await db.scalar(select(IpBlacklist).where(IpBlacklist.id == entry_id).with_for_update())
        if not entry:
            raise NotFoundError("黑名单记录")
        if payload.ip is not None and payload.ip != str(entry.ip):
            existing = await db.scalar(
                select(IpBlacklist.id).where(
                    IpBlacklist.ip == cast(payload.ip, INET), IpBlacklist.id != entry.id
                )
            )
            if existing:
                raise ConflictError("该 IP 已在黑名单中", ErrorCode.IP_BLACKLIST_CONFLICT)
            entry.ip = payload.ip
        if "reason" in payload.model_fields_set:
            entry.reason = payload.reason
        await db.flush()
        response = await _entry_with_creator(db, entry.id)
    return response


@router.delete("/ip-blacklist/{entry_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_ip_blacklist_entry(
    entry_id: UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
) -> Response:
    await db.commit()
    async with db.begin():
        entry = await db.scalar(select(IpBlacklist).where(IpBlacklist.id == entry_id).with_for_update())
        if not entry:
            raise NotFoundError("黑名单记录")
        await db.delete(entry)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
