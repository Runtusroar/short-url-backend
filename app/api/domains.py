from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ErrorCode
from app.database import get_db
from app.db.models import Domain, ShortLink, User, UserDomainAccess, UserRole
from app.dependencies import get_current_user, require_admin
from app.exceptions import ConflictError, NotFoundError
from app.schemas.common import Page
from app.schemas.domain import DomainCreate, DomainResponse, DomainUpdate


router = APIRouter(prefix="/api/domains", tags=["domains"])


@router.get("", response_model=Page[DomainResponse])
async def list_domains(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    statement = select(Domain).where(Domain.is_active.is_(True))
    count_statement = select(func.count()).select_from(Domain).where(Domain.is_active.is_(True))
    if current_user.role != UserRole.ADMIN:
        statement = statement.join(UserDomainAccess).where(UserDomainAccess.user_id == current_user.id)
        count_statement = (
            count_statement.join(UserDomainAccess).where(UserDomainAccess.user_id == current_user.id)
        )
    total = await db.scalar(count_statement) or 0
    result = await db.execute(
        statement.order_by(Domain.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    )
    return Page(items=list(result.scalars()), page=page, page_size=page_size, total=total)


@router.post("", response_model=DomainResponse, status_code=status.HTTP_201_CREATED)
async def create_domain(
    payload: DomainCreate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    await db.commit()
    async with db.begin():
        existing = await db.scalar(select(Domain.id).where(Domain.name == payload.name))
        if existing:
            raise ConflictError("域名已存在", ErrorCode.DOMAIN_CONFLICT)
        domain = Domain(name=payload.name, is_active=payload.is_active)
        db.add(domain)
        await db.flush()
    return domain


@router.put("/{domain_id}", response_model=DomainResponse)
async def update_domain(
    domain_id: UUID,
    payload: DomainUpdate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    await db.commit()
    async with db.begin():
        domain = await db.scalar(select(Domain).where(Domain.id == domain_id).with_for_update())
        if not domain:
            raise NotFoundError("域名")
        if payload.name is not None and payload.name != domain.name:
            existing = await db.scalar(
                select(Domain.id).where(Domain.name == payload.name, Domain.id != domain.id)
            )
            if existing:
                raise ConflictError("域名已存在", ErrorCode.DOMAIN_CONFLICT)
            domain.name = payload.name
        if payload.is_active is not None:
            domain.is_active = payload.is_active
        await db.flush()
    return domain


@router.delete("/{domain_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_domain(
    domain_id: UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
) -> Response:
    await db.commit()
    async with db.begin():
        domain = await db.scalar(select(Domain).where(Domain.id == domain_id).with_for_update())
        if not domain:
            raise NotFoundError("域名")
        referenced = await db.scalar(select(ShortLink.id).where(ShortLink.domain_id == domain.id).limit(1))
        if referenced:
            raise ConflictError("域名仍被短链接引用", ErrorCode.DOMAIN_IN_USE)
        await db.delete(domain)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
