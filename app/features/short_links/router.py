from datetime import date
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import get_current_user, require_staff
from app.features.domains.dependencies import require_domain_access
from app.features.short_links.schemas import (
    AccessRuleCreate,
    AccessRuleResponse,
    AccessRuleUpdate,
    ShortLinkCreate,
    ShortLinkDetail,
    ShortLinkPermissionCreate,
    ShortLinkPermissionResponse,
    ShortLinkResponse,
    ShortLinkUpdate,
    TargetUrlCreate,
    TargetUrlResponse,
    TargetUrlUpdate,
)
from app.features.short_links.service import (
    add_access_rule as add_access_rule_workflow,
    add_target_url as add_target_url_workflow,
    create_short_link as create_short_link_workflow,
    daily_stats_for_links as daily_stats_for_links_workflow,
    delete_access_rule as delete_access_rule_workflow,
    delete_short_link as delete_short_link_workflow,
    delete_target_url as delete_target_url_workflow,
    get_short_link as get_short_link_workflow,
    grant_permission as grant_permission_workflow,
    list_short_links as list_short_links_workflow,
    revoke_permission as revoke_permission_workflow,
    update_access_rule as update_access_rule_workflow,
    update_short_link as update_short_link_workflow,
    update_target_url as update_target_url_workflow,
)
from app.models import Domain, User

router = APIRouter(prefix="/api/short-links", tags=["short-links"])


@router.get("", response_model=list[ShortLinkResponse])
async def list_short_links(
    domain_id: UUID | None = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_domain: Domain = Depends(require_domain_access),
):
    return await list_short_links_workflow(
        db, current_user, current_domain, domain_id
    )


@router.post("", response_model=ShortLinkResponse)
async def create_short_link(
    payload: ShortLinkCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_staff),
):
    return await create_short_link_workflow(db, current_user, payload)


@router.get("/daily-stats")
async def daily_stats_for_links(
    stats_date: date | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_staff),
    current_domain: Domain = Depends(require_domain_access),
):
    return await daily_stats_for_links_workflow(
        db, current_user, current_domain, stats_date
    )


@router.get("/{link_id}", response_model=ShortLinkDetail)
async def get_short_link(
    link_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_domain: Domain = Depends(require_domain_access),
):
    return await get_short_link_workflow(db, link_id, current_user, current_domain)


@router.put("/{link_id}", response_model=ShortLinkResponse)
async def update_short_link(
    link_id: UUID,
    payload: ShortLinkUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_staff),
    current_domain: Domain = Depends(require_domain_access),
):
    return await update_short_link_workflow(
        db, link_id, current_user, current_domain, payload
    )


@router.delete("/{link_id}")
async def delete_short_link(
    link_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_staff),
    current_domain: Domain = Depends(require_domain_access),
):
    await delete_short_link_workflow(db, link_id, current_user, current_domain)
    return {"detail": "Deleted"}


@router.post("/{link_id}/urls", response_model=TargetUrlResponse)
async def add_target_url(
    link_id: UUID,
    payload: TargetUrlCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_staff),
    current_domain: Domain = Depends(require_domain_access),
):
    return await add_target_url_workflow(
        db, link_id, current_user, current_domain, payload
    )


@router.put("/{link_id}/urls/{url_id}", response_model=TargetUrlResponse)
async def update_target_url(
    link_id: UUID,
    url_id: UUID,
    payload: TargetUrlUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_staff),
    current_domain: Domain = Depends(require_domain_access),
):
    return await update_target_url_workflow(
        db, link_id, url_id, current_user, current_domain, payload
    )


@router.delete("/{link_id}/urls/{url_id}")
async def delete_target_url(
    link_id: UUID,
    url_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_staff),
    current_domain: Domain = Depends(require_domain_access),
):
    await delete_target_url_workflow(
        db, link_id, url_id, current_user, current_domain
    )
    return {"detail": "Deleted"}


@router.post("/{link_id}/rules", response_model=AccessRuleResponse)
async def add_access_rule(
    link_id: UUID,
    payload: AccessRuleCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_staff),
    current_domain: Domain = Depends(require_domain_access),
):
    return await add_access_rule_workflow(
        db, link_id, current_user, current_domain, payload
    )


@router.put("/{link_id}/rules/{rule_id}", response_model=AccessRuleResponse)
async def update_access_rule(
    link_id: UUID,
    rule_id: UUID,
    payload: AccessRuleUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_staff),
    current_domain: Domain = Depends(require_domain_access),
):
    return await update_access_rule_workflow(
        db, link_id, rule_id, current_user, current_domain, payload
    )


@router.delete("/{link_id}/rules/{rule_id}")
async def delete_access_rule(
    link_id: UUID,
    rule_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_staff),
    current_domain: Domain = Depends(require_domain_access),
):
    await delete_access_rule_workflow(
        db, link_id, rule_id, current_user, current_domain
    )
    return {"detail": "Deleted"}


@router.post("/{link_id}/permissions", response_model=ShortLinkPermissionResponse)
async def grant_permission(
    link_id: UUID,
    payload: ShortLinkPermissionCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_staff),
    current_domain: Domain = Depends(require_domain_access),
):
    return await grant_permission_workflow(
        db, link_id, current_user, current_domain, payload
    )


@router.delete("/{link_id}/permissions/{user_id}")
async def revoke_permission(
    link_id: UUID,
    user_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_staff),
    current_domain: Domain = Depends(require_domain_access),
):
    await revoke_permission_workflow(
        db, link_id, user_id, current_user, current_domain
    )
    return {"detail": "Revoked"}
