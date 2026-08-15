from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import get_current_user, require_admin
from app.features.domains.schemas import DomainCreate, DomainResponse, DomainUpdate
from app.features.domains.service import (
    create_domain as create_domain_workflow,
    delete_domain as delete_domain_workflow,
    get_domain as get_domain_workflow,
    list_domains as list_domains_workflow,
    update_domain as update_domain_workflow,
)
from app.models import User

router = APIRouter(prefix="/api/domains", tags=["domains"])


@router.get("", response_model=list[DomainResponse])
async def list_domains(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await list_domains_workflow(db, current_user)


@router.post("", response_model=DomainResponse)
async def create_domain(
    payload: DomainCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    return await create_domain_workflow(db, payload)


@router.get("/{domain_id}", response_model=DomainResponse)
async def get_domain(
    domain_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    return await get_domain_workflow(db, domain_id)


@router.put("/{domain_id}", response_model=DomainResponse)
async def update_domain(
    domain_id: UUID,
    payload: DomainUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    return await update_domain_workflow(db, domain_id, payload)


@router.delete("/{domain_id}")
async def delete_domain(
    domain_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    await delete_domain_workflow(db, domain_id)
    return {"detail": "Deleted"}
