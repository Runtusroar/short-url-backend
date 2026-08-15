from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError, NotFoundError
from app.features.domains.schemas import DomainCreate, DomainUpdate
from app.models import Domain, User, UserDomain


async def list_domains(db: AsyncSession, current_user: User) -> list[Domain]:
    if current_user.role == "admin":
        result = await db.execute(select(Domain).order_by(Domain.created_at.desc()))
    else:
        result = await db.execute(
            select(Domain)
            .join(UserDomain, UserDomain.domain_id == Domain.id)
            .where(UserDomain.user_id == current_user.id)
            .order_by(Domain.created_at.desc())
        )
    return result.scalars().all()


async def create_domain(db: AsyncSession, payload: DomainCreate) -> Domain:
    existing = await db.execute(select(Domain).where(Domain.name == payload.name))
    if existing.scalar_one_or_none():
        raise ConflictError("域名已存在")

    if payload.is_default:
        await db.execute(Domain.__table__.update().values(is_default=False))

    domain = Domain(**payload.model_dump())
    db.add(domain)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise ConflictError("域名已存在") from exc
    await db.refresh(domain)
    return domain


async def get_domain(db: AsyncSession, domain_id: UUID) -> Domain:
    result = await db.execute(select(Domain).where(Domain.id == domain_id))
    domain = result.scalar_one_or_none()
    if not domain:
        raise NotFoundError("域名")
    return domain


async def update_domain(
    db: AsyncSession, domain_id: UUID, payload: DomainUpdate
) -> Domain:
    domain = await get_domain(db, domain_id)

    if payload.name is not None:
        existing = await db.execute(
            select(Domain).where(Domain.name == payload.name, Domain.id != domain_id)
        )
        if existing.scalar_one_or_none():
            raise ConflictError("域名已存在")

    if payload.is_default:
        await db.execute(Domain.__table__.update().values(is_default=False))

    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(domain, field, value)

    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise ConflictError("域名已存在") from exc
    await db.refresh(domain)
    return domain


async def delete_domain(db: AsyncSession, domain_id: UUID) -> None:
    domain = await get_domain(db, domain_id)
    domain.is_active = False
    domain.is_default = False
    await db.commit()
