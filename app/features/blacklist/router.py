from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import require_staff
from app.features.blacklist.schemas import IpBlacklistCreate, IpBlacklistResponse
from app.features.blacklist.service import (
    add_to_blacklist as add_to_blacklist_workflow,
    list_blacklist as list_blacklist_workflow,
    remove_from_blacklist as remove_from_blacklist_workflow,
)
from app.models import User

router = APIRouter(prefix="/api/ip-blacklist", tags=["ip-blacklist"])


@router.get("", response_model=list[IpBlacklistResponse])
async def list_blacklist(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_staff),
):
    return await list_blacklist_workflow(db)


@router.post("", response_model=IpBlacklistResponse)
async def add_to_blacklist(
    payload: IpBlacklistCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_staff),
):
    return await add_to_blacklist_workflow(db, payload, current_user)


@router.delete("/{entry_id}")
async def remove_from_blacklist(
    entry_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_staff),
):
    await remove_from_blacklist_workflow(db, entry_id, current_user)
    return {"detail": "Removed"}
