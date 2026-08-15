from datetime import date, datetime
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Query
from sqlalchemy import distinct, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import get_current_user, require_staff
from app.domains import require_domain_access
from app.core.exceptions import APIError, ConflictError, NotFoundError, PermissionDeniedError
from app.models import AccessLog, AccessRule, Domain, ShortLink, ShortLinkPermission, TargetUrl, User, UserDomain
from app.schemas import (
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
from app.services.short_code import create_unique_short_code

router = APIRouter(prefix="/api/short-links", tags=["short-links"])


async def _resolve_effective_domain(
    domain_id: UUID | None,
    current_user: User,
    current_domain: Domain,
    db: AsyncSession,
) -> Domain:
    """Admin can override via domain_id; others use current domain from Host header."""
    if domain_id and current_user.role == "admin":
        result = await db.execute(select(Domain).where(Domain.id == domain_id, Domain.is_active == True))
        domain = result.scalar_one_or_none()
        if not domain:
            raise NotFoundError("域名")
        return domain
    return current_domain


async def _can_manage_link(user: User, link: ShortLink) -> bool:
    if user.role == "admin":
        return True
    return str(link.owner_id) == str(user.id)


async def _has_domain(user: User, domain_id: UUID, db: AsyncSession) -> bool:
    if user.role == "admin":
        return True
    result = await db.execute(
        select(UserDomain).where(
            UserDomain.user_id == user.id,
            UserDomain.domain_id == domain_id,
        )
    )
    return result.scalar_one_or_none() is not None


async def _get_short_link(db: AsyncSession, link_id: UUID, user: User) -> ShortLink:
    result = await db.execute(select(ShortLink).where(ShortLink.id == link_id))
    link = result.scalar_one_or_none()
    if not link:
        raise NotFoundError("短链")
    if not await _can_manage_link(user, link):
        raise PermissionDeniedError()
    return link


async def _get_managed_link(
    db: AsyncSession,
    link_id: UUID,
    current_user: User,
    current_domain: Domain,
) -> ShortLink:
    """Fetch a short link and verify it belongs to the current domain (non-admins)."""
    link = await _get_short_link(db, link_id, current_user)
    if current_user.role != "admin" and str(link.domain_id) != str(current_domain.id):
        raise PermissionDeniedError("短链不属于当前域名")
    return link


async def _check_managed_link(
    db: AsyncSession,
    link_id: UUID,
    current_user: User,
    current_domain: Domain,
) -> None:
    """Verify the user can manage the short link in the current domain."""
    await _get_managed_link(db, link_id, current_user, current_domain)


@router.get("", response_model=list[ShortLinkResponse])
async def list_short_links(
    domain_id: UUID | None = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_domain: Domain = Depends(require_domain_access),
):
    effective_domain = await _resolve_effective_domain(domain_id, current_user, current_domain, db)
    base_query = select(ShortLink).where(ShortLink.domain_id == effective_domain.id)

    if current_user.role == "admin":
        query = base_query
    elif current_user.role == "operator":
        query = base_query.where(ShortLink.owner_id == current_user.id)
    else:  # client
        query = (
            base_query.join(ShortLinkPermission, ShortLinkPermission.short_link_id == ShortLink.id)
            .where(ShortLinkPermission.user_id == current_user.id)
        )

    result = await db.execute(query.order_by(ShortLink.created_at.desc()))
    return result.scalars().all()


@router.post("", response_model=ShortLinkResponse)
async def create_short_link(
    payload: ShortLinkCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_staff),
):
    domain = await db.execute(select(Domain).where(Domain.id == payload.domain_id))
    domain = domain.scalar_one_or_none()
    if not domain:
        raise NotFoundError("域名")
    if not domain.is_active:
        raise APIError(code="DOMAIN_INACTIVE", message="域名已停用", status_code=400)

    if not await _has_domain(current_user, payload.domain_id, db):
        raise PermissionDeniedError("无权访问该域名")

    try:
        short_code = await create_unique_short_code(db, payload.domain_id, payload.custom_alias)
    except ValueError as exc:
        raise APIError(code="INVALID_SHORT_CODE", message=str(exc), status_code=400) from exc

    link = ShortLink(
        domain_id=payload.domain_id,
        short_code=short_code,
        is_custom_alias=bool(payload.custom_alias),
        description=payload.description,
        default_action="deny",
        owner_id=current_user.id,
    )
    db.add(link)
    await db.flush()

    for url in payload.normal_urls:
        db.add(TargetUrl(
            short_link_id=link.id,
            url=url,
            url_type="allowed",
            weight=1,
            is_active=True,
        ))
    for url in payload.blocked_urls:
        db.add(TargetUrl(
            short_link_id=link.id,
            url=url,
            url_type="denied",
            weight=1,
            is_active=True,
        ))

    await db.commit()
    await db.refresh(link)
    return link


@router.get("/daily-stats")
async def daily_stats_for_links(
    stats_date: date | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_staff),
    current_domain: Domain = Depends(require_domain_access),
):
    """Return today's visit count for each short link in the current domain."""
    if stats_date is None:
        stats_date = datetime.now(ZoneInfo("Asia/Shanghai")).date()

    base_query = select(ShortLink).where(ShortLink.domain_id == current_domain.id)
    if current_user.role != "admin":
        base_query = base_query.where(ShortLink.owner_id == current_user.id)

    result = await db.execute(base_query.order_by(ShortLink.created_at.desc()))
    links = result.scalars().all()
    link_ids = [link.id for link in links]

    stats = {}
    if link_ids:
        result = await db.execute(
            select(
                AccessLog.short_link_id,
                func.count().label("total"),
                func.count(distinct(AccessLog.ip)).label("unique_ips"),
            )
            .where(
                AccessLog.short_link_id.in_(link_ids),
                AccessLog.domain_id == current_domain.id,
                AccessLog.accessed_at_plus8 == stats_date,
            )
            .group_by(AccessLog.short_link_id)
        )
        stats = {
            row.short_link_id: {
                "total": row.total,
                "unique_ips": row.unique_ips,
            }
            for row in result.all()
        }

    return [
        {
            "short_link_id": str(link.id),
            "short_code": link.short_code,
            "total": stats.get(link.id, {}).get("total", 0),
            "unique_ips": stats.get(link.id, {}).get("unique_ips", 0),
        }
        for link in links
    ]


@router.get("/{link_id}", response_model=ShortLinkDetail)
async def get_short_link(
    link_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_domain: Domain = Depends(require_domain_access),
):
    result = await db.execute(select(ShortLink).where(ShortLink.id == link_id))
    link = result.scalar_one_or_none()
    if not link:
        raise NotFoundError("短链")

    # client must have explicit permission and domain access
    if current_user.role == "client":
        if str(link.domain_id) != str(current_domain.id):
            raise PermissionDeniedError()
        perm = await db.execute(
            select(ShortLinkPermission).where(
                ShortLinkPermission.short_link_id == link_id,
                ShortLinkPermission.user_id == current_user.id,
            )
        )
        if not perm.scalar_one_or_none():
            raise PermissionDeniedError()
    elif not await _can_manage_link(current_user, link):
        raise PermissionDeniedError()

    return link


@router.put("/{link_id}", response_model=ShortLinkResponse)
async def update_short_link(
    link_id: UUID,
    payload: ShortLinkUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_staff),
    current_domain: Domain = Depends(require_domain_access),
):
    link = await _get_managed_link(db, link_id, current_user, current_domain)
    if payload.description is not None:
        link.description = payload.description
    if payload.is_active is not None:
        link.is_active = payload.is_active
    await db.commit()
    await db.refresh(link)
    return link


@router.delete("/{link_id}")
async def delete_short_link(
    link_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_staff),
    current_domain: Domain = Depends(require_domain_access),
):
    link = await _get_managed_link(db, link_id, current_user, current_domain)
    await db.delete(link)
    await db.commit()
    return {"detail": "Deleted"}


# Target URLs


@router.post("/{link_id}/urls", response_model=TargetUrlResponse)
async def add_target_url(
    link_id: UUID,
    payload: TargetUrlCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_staff),
    current_domain: Domain = Depends(require_domain_access),
):
    link = await _get_managed_link(db, link_id, current_user, current_domain)
    url = TargetUrl(short_link_id=link.id, **payload.model_dump())
    db.add(url)
    await db.commit()
    await db.refresh(url)
    return url


@router.put("/{link_id}/urls/{url_id}", response_model=TargetUrlResponse)
async def update_target_url(
    link_id: UUID,
    url_id: UUID,
    payload: TargetUrlUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_staff),
    current_domain: Domain = Depends(require_domain_access),
):
    await _check_managed_link(db, link_id, current_user, current_domain)
    result = await db.execute(select(TargetUrl).where(TargetUrl.id == url_id, TargetUrl.short_link_id == link_id))
    url = result.scalar_one_or_none()
    if not url:
        raise NotFoundError("目标URL")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(url, field, value)
    await db.commit()
    await db.refresh(url)
    return url


@router.delete("/{link_id}/urls/{url_id}")
async def delete_target_url(
    link_id: UUID,
    url_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_staff),
    current_domain: Domain = Depends(require_domain_access),
):
    await _check_managed_link(db, link_id, current_user, current_domain)
    result = await db.execute(select(TargetUrl).where(TargetUrl.id == url_id, TargetUrl.short_link_id == link_id))
    url = result.scalar_one_or_none()
    if not url:
        raise NotFoundError("目标URL")
    await db.delete(url)
    await db.commit()
    return {"detail": "Deleted"}


# Access Rules


@router.post("/{link_id}/rules", response_model=AccessRuleResponse)
async def add_access_rule(
    link_id: UUID,
    payload: AccessRuleCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_staff),
    current_domain: Domain = Depends(require_domain_access),
):
    link = await _get_managed_link(db, link_id, current_user, current_domain)
    rule = AccessRule(short_link_id=link.id, **payload.model_dump())
    db.add(rule)
    await db.commit()
    await db.refresh(rule)
    return rule


@router.put("/{link_id}/rules/{rule_id}", response_model=AccessRuleResponse)
async def update_access_rule(
    link_id: UUID,
    rule_id: UUID,
    payload: AccessRuleUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_staff),
    current_domain: Domain = Depends(require_domain_access),
):
    await _check_managed_link(db, link_id, current_user, current_domain)
    result = await db.execute(
        select(AccessRule).where(AccessRule.id == rule_id, AccessRule.short_link_id == link_id)
    )
    rule = result.scalar_one_or_none()
    if not rule:
        raise NotFoundError("访问规则")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(rule, field, value)
    await db.commit()
    await db.refresh(rule)
    return rule


@router.delete("/{link_id}/rules/{rule_id}")
async def delete_access_rule(
    link_id: UUID,
    rule_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_staff),
    current_domain: Domain = Depends(require_domain_access),
):
    await _check_managed_link(db, link_id, current_user, current_domain)
    result = await db.execute(
        select(AccessRule).where(AccessRule.id == rule_id, AccessRule.short_link_id == link_id)
    )
    rule = result.scalar_one_or_none()
    if not rule:
        raise NotFoundError("访问规则")
    await db.delete(rule)
    await db.commit()
    return {"detail": "Deleted"}


# Permissions


@router.post("/{link_id}/permissions", response_model=ShortLinkPermissionResponse)
async def grant_permission(
    link_id: UUID,
    payload: ShortLinkPermissionCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_staff),
    current_domain: Domain = Depends(require_domain_access),
):
    link = await _get_managed_link(db, link_id, current_user, current_domain)

    user_result = await db.execute(select(User).where(User.id == payload.user_id, User.role == "client"))
    user = user_result.scalar_one_or_none()
    if not user:
        raise NotFoundError("目标用户（需为 client 角色）")

    # client user must have access to current domain
    if not await _has_domain(user, current_domain.id, db):
        raise PermissionDeniedError("该 client 用户无权访问此域名")

    perm = ShortLinkPermission(short_link_id=link.id, user_id=user.id)
    db.add(perm)
    try:
        await db.commit()
        await db.refresh(perm)
    except Exception as exc:
        await db.rollback()
        raise ConflictError("该用户已被授权查看此短链") from exc
    return perm


@router.delete("/{link_id}/permissions/{user_id}")
async def revoke_permission(
    link_id: UUID,
    user_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_staff),
    current_domain: Domain = Depends(require_domain_access),
):
    await _check_managed_link(db, link_id, current_user, current_domain)
    result = await db.execute(
        select(ShortLinkPermission).where(
            ShortLinkPermission.short_link_id == link_id,
            ShortLinkPermission.user_id == user_id,
        )
    )
    perm = result.scalar_one_or_none()
    if not perm:
        raise NotFoundError("授权记录")
    await db.delete(perm)
    await db.commit()
    return {"detail": "Revoked"}
