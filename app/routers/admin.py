from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_password_hash, require_admin
from app.database import get_db
from app.exceptions import ConflictError, NotFoundError, PermissionDeniedError
from app.models import Domain, User, UserDomain
from app.schemas import UserCreate, UserResponse

router = APIRouter(prefix="/api/admin", tags=["admin"])


@router.get("/users", response_model=list[UserResponse])
async def list_users(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    result = await db.execute(select(User).order_by(User.created_at.desc()))
    return result.scalars().all()


@router.post("/users", response_model=UserResponse)
async def create_user(
    payload: UserCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    existing = await db.execute(select(User).where(User.username == payload.username))
    if existing.scalar_one_or_none():
        raise ConflictError("用户名已存在")

    for domain_id in payload.domain_ids:
        domain = await db.execute(select(Domain).where(Domain.id == domain_id))
        if not domain.scalar_one_or_none():
            raise NotFoundError("指定域名")

    user = User(
        username=payload.username,
        password_hash=get_password_hash(payload.password),
        role=payload.role,
    )
    db.add(user)
    await db.flush()

    for domain_id in payload.domain_ids:
        db.add(UserDomain(user_id=user.id, domain_id=domain_id))

    await db.commit()
    await db.refresh(user)
    return user


@router.put("/users/{user_id}", response_model=UserResponse)
async def update_user(
    user_id: UUID,
    payload: UserCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise NotFoundError("用户")

    # Check username uniqueness excluding current user
    existing = await db.execute(select(User).where(User.username == payload.username, User.id != user_id))
    if existing.scalar_one_or_none():
        raise ConflictError("用户名已存在")

    for domain_id in payload.domain_ids:
        domain = await db.execute(select(Domain).where(Domain.id == domain_id))
        if not domain.scalar_one_or_none():
            raise NotFoundError("指定域名")

    user.username = payload.username
    user.password_hash = get_password_hash(payload.password)
    user.role = payload.role

    await db.execute(UserDomain.__table__.delete().where(UserDomain.user_id == user.id))
    for domain_id in payload.domain_ids:
        db.add(UserDomain(user_id=user.id, domain_id=domain_id))

    await db.commit()
    await db.refresh(user)
    return user


@router.delete("/users/{user_id}")
async def delete_user(
    user_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise NotFoundError("用户")
    await db.delete(user)
    await db.commit()
    return {"detail": "Deleted"}
