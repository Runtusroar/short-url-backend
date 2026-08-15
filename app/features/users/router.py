from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import require_admin
from app.features.users.schemas import UserCreate, UserResponse
from app.features.users.service import (
    create_user as create_user_workflow,
    delete_user as delete_user_workflow,
    list_users as list_users_workflow,
    update_user as update_user_workflow,
)
from app.models import User

router = APIRouter(prefix="/api/admin/users", tags=["admin"])


@router.get("", response_model=list[UserResponse])
async def list_users(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    return await list_users_workflow(db)


@router.post("", response_model=UserResponse)
async def create_user(
    payload: UserCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    return await create_user_workflow(db, payload)


@router.put("/{user_id}", response_model=UserResponse)
async def update_user(
    user_id: UUID,
    payload: UserCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    return await update_user_workflow(db, user_id, payload)


@router.delete("/{user_id}")
async def delete_user(
    user_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    await delete_user_workflow(db, user_id)
    return {"detail": "Deleted"}
