from datetime import UTC, date, datetime
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import distinct, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import (
    APIError,
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
)
from app.features.short_links.schemas import (
    AccessRuleCreate,
    AccessRuleUpdate,
    ShortLinkCreate,
    ShortLinkPermissionCreate,
    ShortLinkUpdate,
    TargetUrlCreate,
    TargetUrlUpdate,
)
from app.features.short_links.short_code import create_unique_short_code
from app.models import (
    AccessLog,
    AccessRule,
    Domain,
    ShortLink,
    ShortLinkPermission,
    TargetUrl,
    User,
    UserDomain,
)


def _current_date_in_timezone(timezone: str, now: datetime | None = None) -> date:
    """Return the local calendar date for one UTC instant in a domain timezone."""
    instant = now if now is not None else datetime.now(UTC)
    return instant.astimezone(ZoneInfo(timezone)).date()


async def _resolve_effective_domain(
    domain_id: UUID | None,
    current_user: User,
    current_domain: Domain,
    db: AsyncSession,
) -> Domain:
    """Admin can override via domain_id; others use current domain from Host header."""
    if domain_id and current_user.role == "admin":
        result = await db.execute(
            select(Domain).where(Domain.id == domain_id, Domain.is_active == True)
        )
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
    result = await db.execute(
        select(ShortLink).where(ShortLink.id == link_id, ShortLink.deleted_at.is_(None))
    )
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


async def list_short_links(
    db: AsyncSession,
    current_user: User,
    current_domain: Domain,
    domain_id: UUID | None,
) -> list[ShortLink]:
    effective_domain = await _resolve_effective_domain(
        domain_id, current_user, current_domain, db
    )
    base_query = select(ShortLink).where(
        ShortLink.domain_id == effective_domain.id, ShortLink.deleted_at.is_(None)
    )

    if current_user.role == "admin":
        query = base_query
    elif current_user.role == "operator":
        query = base_query.where(ShortLink.owner_id == current_user.id)
    else:  # client
        query = base_query.join(
            ShortLinkPermission, ShortLinkPermission.short_link_id == ShortLink.id
        ).where(ShortLinkPermission.user_id == current_user.id)

    result = await db.execute(query.order_by(ShortLink.created_at.desc()))
    return result.scalars().all()


async def create_short_link(
    db: AsyncSession,
    current_user: User,
    payload: ShortLinkCreate,
) -> ShortLink:
    domain = await db.execute(select(Domain).where(Domain.id == payload.domain_id))
    domain = domain.scalar_one_or_none()
    if not domain:
        raise NotFoundError("域名")
    if not domain.is_active:
        raise APIError(code="DOMAIN_INACTIVE", message="域名已停用", status_code=400)

    if not await _has_domain(current_user, payload.domain_id, db):
        raise PermissionDeniedError("无权访问该域名")

    try:
        short_code = await create_unique_short_code(
            db, payload.domain_id, payload.custom_alias
        )
    except ValueError as exc:
        raise APIError(
            code="INVALID_SHORT_CODE", message=str(exc), status_code=400
        ) from exc

    link = ShortLink(
        domain_id=payload.domain_id,
        short_code=short_code,
        is_custom_alias=bool(payload.custom_alias),
        name=payload.name,
        default_action="deny",
        owner_id=current_user.id,
    )
    db.add(link)
    await db.flush()

    for url in payload.normal_urls:
        db.add(
            TargetUrl(
                short_link_id=link.id,
                url=url,
                url_type="allowed",
                weight=1,
                is_active=True,
            )
        )
    for url in payload.blocked_urls:
        db.add(
            TargetUrl(
                short_link_id=link.id,
                url=url,
                url_type="denied",
                weight=1,
                is_active=True,
            )
        )

    await db.commit()
    await db.refresh(link)
    return link


async def daily_stats_for_links(
    db: AsyncSession,
    current_user: User,
    current_domain: Domain,
    stats_date: date | None,
) -> list[dict]:
    """Return today's visit count for each short link in the current domain."""
    if stats_date is None:
        stats_date = _current_date_in_timezone(current_domain.timezone)

    base_query = select(ShortLink).where(
        ShortLink.domain_id == current_domain.id, ShortLink.deleted_at.is_(None)
    )
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
                func.count(distinct(AccessLog.client_ip)).label("unique_ips"),
            )
            .where(
                AccessLog.short_link_id.in_(link_ids),
                AccessLog.domain_id == current_domain.id,
                AccessLog.access_date == stats_date,
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


async def get_short_link(
    db: AsyncSession,
    link_id: UUID,
    current_user: User,
    current_domain: Domain,
) -> ShortLink:
    result = await db.execute(
        select(ShortLink).where(ShortLink.id == link_id, ShortLink.deleted_at.is_(None))
    )
    link = result.scalar_one_or_none()
    if not link:
        raise NotFoundError("短链")

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


async def update_short_link(
    db: AsyncSession,
    link_id: UUID,
    current_user: User,
    current_domain: Domain,
    payload: ShortLinkUpdate,
) -> ShortLink:
    link = await _get_managed_link(db, link_id, current_user, current_domain)
    if payload.name is not None:
        link.name = payload.name
    if payload.is_active is not None:
        link.is_active = payload.is_active
    await db.commit()
    await db.refresh(link)
    return link


async def delete_short_link(
    db: AsyncSession,
    link_id: UUID,
    current_user: User,
    current_domain: Domain,
) -> None:
    link = await _get_managed_link(db, link_id, current_user, current_domain)
    link.is_active = False
    link.deleted_at = datetime.now(UTC)
    link.deleted_by = current_user.id
    await db.commit()


async def add_target_url(
    db: AsyncSession,
    link_id: UUID,
    current_user: User,
    current_domain: Domain,
    payload: TargetUrlCreate,
) -> TargetUrl:
    link = await _get_managed_link(db, link_id, current_user, current_domain)
    url = TargetUrl(short_link_id=link.id, **payload.model_dump())
    db.add(url)
    await db.commit()
    await db.refresh(url)
    return url


async def update_target_url(
    db: AsyncSession,
    link_id: UUID,
    url_id: UUID,
    current_user: User,
    current_domain: Domain,
    payload: TargetUrlUpdate,
) -> TargetUrl:
    await _check_managed_link(db, link_id, current_user, current_domain)
    result = await db.execute(
        select(TargetUrl).where(
            TargetUrl.id == url_id, TargetUrl.short_link_id == link_id
        )
    )
    url = result.scalar_one_or_none()
    if not url:
        raise NotFoundError("目标URL")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(url, field, value)
    await db.commit()
    await db.refresh(url)
    return url


async def delete_target_url(
    db: AsyncSession,
    link_id: UUID,
    url_id: UUID,
    current_user: User,
    current_domain: Domain,
) -> None:
    await _check_managed_link(db, link_id, current_user, current_domain)
    result = await db.execute(
        select(TargetUrl).where(
            TargetUrl.id == url_id, TargetUrl.short_link_id == link_id
        )
    )
    url = result.scalar_one_or_none()
    if not url:
        raise NotFoundError("目标URL")
    await db.delete(url)
    await db.commit()


async def add_access_rule(
    db: AsyncSession,
    link_id: UUID,
    current_user: User,
    current_domain: Domain,
    payload: AccessRuleCreate,
) -> AccessRule:
    link = await _get_managed_link(db, link_id, current_user, current_domain)
    values = payload.model_dump()
    priority = values.pop("priority")
    if priority is None:
        priority = await db.scalar(
            select(func.coalesce(func.max(AccessRule.priority), -1) + 1).where(
                AccessRule.short_link_id == link.id
            )
        )
    rule = AccessRule(short_link_id=link.id, priority=priority, **values)
    db.add(rule)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise ConflictError("该优先级已存在") from exc
    await db.refresh(rule)
    return rule


async def update_access_rule(
    db: AsyncSession,
    link_id: UUID,
    rule_id: UUID,
    current_user: User,
    current_domain: Domain,
    payload: AccessRuleUpdate,
) -> AccessRule:
    await _check_managed_link(db, link_id, current_user, current_domain)
    result = await db.execute(
        select(AccessRule).where(
            AccessRule.id == rule_id, AccessRule.short_link_id == link_id
        )
    )
    rule = result.scalar_one_or_none()
    if not rule:
        raise NotFoundError("访问规则")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(rule, field, value)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise ConflictError("该优先级已存在") from exc
    await db.refresh(rule)
    return rule


async def delete_access_rule(
    db: AsyncSession,
    link_id: UUID,
    rule_id: UUID,
    current_user: User,
    current_domain: Domain,
) -> None:
    await _check_managed_link(db, link_id, current_user, current_domain)
    result = await db.execute(
        select(AccessRule).where(
            AccessRule.id == rule_id, AccessRule.short_link_id == link_id
        )
    )
    rule = result.scalar_one_or_none()
    if not rule:
        raise NotFoundError("访问规则")
    await db.delete(rule)
    await db.commit()


async def grant_permission(
    db: AsyncSession,
    link_id: UUID,
    current_user: User,
    current_domain: Domain,
    payload: ShortLinkPermissionCreate,
) -> ShortLinkPermission:
    link = await _get_managed_link(db, link_id, current_user, current_domain)

    user_result = await db.execute(
        select(User).where(User.id == payload.user_id, User.role == "client")
    )
    user = user_result.scalar_one_or_none()
    if not user:
        raise NotFoundError("目标用户（需为 client 角色）")

    if not await _has_domain(user, current_domain.id, db):
        raise PermissionDeniedError("该 client 用户无权访问此域名")

    perm = ShortLinkPermission(
        short_link_id=link.id, user_id=user.id, granted_by=current_user.id
    )
    db.add(perm)
    try:
        await db.commit()
        await db.refresh(perm)
    except Exception as exc:
        await db.rollback()
        raise ConflictError("该用户已被授权查看此短链") from exc
    return perm


async def revoke_permission(
    db: AsyncSession,
    link_id: UUID,
    user_id: UUID,
    current_user: User,
    current_domain: Domain,
) -> None:
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
