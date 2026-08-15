from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_admin
from app.database import get_db
from app.dependencies import get_current_user
from app.models import Domain, User
from app.schemas import DomainCreate, DomainResponse, DomainUpdate

router = APIRouter(prefix="/api/domains", tags=["domains"])


@router.get("", response_model=list[DomainResponse])
async def list_domains(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if current_user.role == "admin":
        result = await db.execute(select(Domain).order_by(Domain.created_at.desc()))
    else:
        from sqlalchemy import distinct
        from app.models import UserDomain

        result = await db.execute(
            select(Domain)
            .join(UserDomain, UserDomain.domain_id == Domain.id)
            .where(UserDomain.user_id == current_user.id)
            .order_by(Domain.created_at.desc())
        )
    return result.scalars().all()


@router.post("", response_model=DomainResponse)
async def create_domain(
    payload: DomainCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    existing = await db.execute(select(Domain).where(Domain.name == payload.name))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Domain already exists")

    domain = Domain(**payload.model_dump())
    db.add(domain)
    await db.commit()
    await db.refresh(domain)
    return domain


@router.get("/{domain_id}", response_model=DomainResponse)
async def get_domain(
    domain_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    result = await db.execute(select(Domain).where(Domain.id == domain_id))
    domain = result.scalar_one_or_none()
    if not domain:
        raise HTTPException(status_code=404, detail="Domain not found")
    return domain


@router.put("/{domain_id}", response_model=DomainResponse)
async def update_domain(
    domain_id: UUID,
    payload: DomainUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    result = await db.execute(select(Domain).where(Domain.id == domain_id))
    domain = result.scalar_one_or_none()
    if not domain:
        raise HTTPException(status_code=404, detail="Domain not found")

    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(domain, field, value)

    await db.commit()
    await db.refresh(domain)
    return domain


@router.delete("/{domain_id}")
async def delete_domain(
    domain_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    result = await db.execute(select(Domain).where(Domain.id == domain_id))
    domain = result.scalar_one_or_none()
    if not domain:
        raise HTTPException(status_code=404, detail="Domain not found")
    await db.delete(domain)
    await db.commit()
    return {"detail": "Deleted"}
