from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError, NotFoundError
from app.core.security import get_password_hash
from app.features.users.schemas import UserCreate
from app.models import Domain, User, UserDomain


async def list_users(db: AsyncSession) -> list[User]:
    result = await db.execute(select(User).order_by(User.created_at.desc()))
    return result.scalars().all()


async def create_user(db: AsyncSession, payload: UserCreate) -> User:
    existing = await db.execute(select(User).where(User.username == payload.username))
    if existing.scalar_one_or_none():
        raise ConflictError("用户名已存在")

    await _validate_domains(db, payload.domain_ids)

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


async def update_user(db: AsyncSession, user_id: UUID, payload: UserCreate) -> User:
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise NotFoundError("用户")

    existing = await db.execute(select(User).where(User.username == payload.username, User.id != user_id))
    if existing.scalar_one_or_none():
        raise ConflictError("用户名已存在")

    await _validate_domains(db, payload.domain_ids)

    user.username = payload.username
    user.password_hash = get_password_hash(payload.password)
    user.role = payload.role

    await db.execute(UserDomain.__table__.delete().where(UserDomain.user_id == user.id))
    for domain_id in payload.domain_ids:
        db.add(UserDomain(user_id=user.id, domain_id=domain_id))

    await db.commit()
    await db.refresh(user)
    return user


async def delete_user(db: AsyncSession, user_id: UUID) -> None:
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise NotFoundError("用户")
    await db.delete(user)
    await db.commit()


async def _validate_domains(db: AsyncSession, domain_ids: list[UUID]) -> None:
    for domain_id in domain_ids:
        domain = await db.execute(select(Domain).where(Domain.id == domain_id))
        if not domain.scalar_one_or_none():
            raise NotFoundError("指定域名")
