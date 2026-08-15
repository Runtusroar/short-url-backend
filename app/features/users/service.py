from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError, NotFoundError
from app.core.security import get_password_hash
from app.features.users.schemas import UserCreate, UserUpdate
from app.models import Domain, User, UserDomain


async def list_users(db: AsyncSession) -> list[User]:
    result = await db.execute(select(User).order_by(User.created_at.desc()))
    return result.scalars().all()


async def create_user(db: AsyncSession, payload: UserCreate, current_user: User) -> User:
    try:
        existing = await db.execute(select(User).where(User.username == payload.username))
        if existing.scalar_one_or_none():
            raise ConflictError("用户名已存在")

        await _validate_domains(db, payload.domain_ids)

        user = User(
            username=payload.username,
            password_hash=get_password_hash(payload.password),
            role=payload.role.value,
        )
        db.add(user)
        await db.flush()

        for domain_id in set(payload.domain_ids):
            db.add(UserDomain(user_id=user.id, domain_id=domain_id, granted_by=current_user.id))

        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise ConflictError("用户名已存在") from exc
    await db.refresh(user)
    return user


async def update_user(
    db: AsyncSession, user_id: UUID, payload: UserUpdate, current_user: User
) -> User:
    try:
        result = await db.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()
        if not user:
            raise NotFoundError("用户")

        if payload.username is not None:
            existing = await db.execute(
                select(User).where(User.username == payload.username, User.id != user_id)
            )
            if existing.scalar_one_or_none():
                raise ConflictError("用户名已存在")

        if payload.domain_ids is not None:
            await _validate_domains(db, payload.domain_ids)

        if payload.username is not None:
            user.username = payload.username
        if payload.password is not None:
            user.password_hash = get_password_hash(payload.password)
        if payload.role is not None:
            user.role = payload.role.value
        if payload.is_active is not None:
            user.is_active = payload.is_active

        if payload.domain_ids is not None:
            mappings = await db.execute(select(UserDomain).where(UserDomain.user_id == user.id))
            current_domain_ids = {mapping.domain_id for mapping in mappings.scalars()}
            requested_domain_ids = set(payload.domain_ids)
            if domain_ids_to_remove := current_domain_ids - requested_domain_ids:
                await db.execute(
                    UserDomain.__table__.delete().where(
                        UserDomain.user_id == user.id,
                        UserDomain.domain_id.in_(domain_ids_to_remove),
                    )
                )
            for domain_id in requested_domain_ids - current_domain_ids:
                db.add(UserDomain(user_id=user.id, domain_id=domain_id, granted_by=current_user.id))

        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise ConflictError("用户名已存在") from exc
    await db.refresh(user)
    return user


async def delete_user(db: AsyncSession, user_id: UUID) -> None:
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise NotFoundError("用户")
    user.is_active = False
    await db.commit()


async def _validate_domains(db: AsyncSession, domain_ids: list[UUID]) -> None:
    for domain_id in domain_ids:
        domain = await db.execute(select(Domain).where(Domain.id == domain_id))
        if not domain.scalar_one_or_none():
            raise NotFoundError("指定域名")
