from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy import delete, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.errors import ConflictError, ErrorCode, NotFoundError, integrity_constraint_name
from app.db.session import get_db
from app.db.models import AccessLevel, LinkPolicy, ShortLink, TargetUrl, User
from app.dependencies import get_current_user
from app.schemas.common import Page
from app.schemas.short_link import (
    DestinationResponse,
    LinkPolicyResponse,
    ShortLinkListItem,
    ShortLinkResponse,
    ShortLinkWrite,
)
from app.services.authorization import authorized_domain_ids_query, ensure_domain_access
from app.services.short_code import create_unique_short_code


router = APIRouter(prefix="/api/short-links", tags=["short-links"])


def _is_short_code_unique_violation(exc: IntegrityError) -> bool:
    """Only the database's named domain/code constraint is a public alias conflict."""
    return integrity_constraint_name(exc) == "uq_domain_short_code"


def _visible_links(user: User):
    return ShortLink.domain_id.in_(authorized_domain_ids_query(user))


def _policy_response(policy: LinkPolicy | None) -> LinkPolicyResponse:
    if policy is None:
        # Historical links can legitimately have no policy after migration.
        return LinkPolicyResponse(updated_at=None)
    return LinkPolicyResponse(
        country_mode=policy.country_mode,
        countries=policy.countries,
        platform_mode=policy.platform_mode,
        platforms=policy.platforms,
        referer_mode=policy.referer_mode,
        referer_patterns=policy.referer_patterns,
        block_proxy=policy.block_proxy,
        block_bot=policy.block_bot,
        updated_at=policy.updated_at,
    )


def _list_item(link: ShortLink) -> ShortLinkListItem:
    return ShortLinkListItem(
        id=link.id,
        domain_id=link.domain_id,
        short_code=link.short_code,
        is_custom_alias=link.is_custom_alias,
        note=link.note,
        owner_id=link.owner_id,
        is_active=link.is_active,
        created_at=link.created_at,
        updated_at=link.updated_at,
    )


def _response(link: ShortLink) -> ShortLinkResponse:
    return ShortLinkResponse(
        **_list_item(link).model_dump(),
        destinations=[
            DestinationResponse(
                id=destination.id,
                url=destination.url,
                type=destination.url_type,
                weight=destination.weight,
                is_active=destination.is_active,
                created_at=destination.created_at,
            )
            for destination in link.target_urls
        ],
        policy=_policy_response(link.policy),
    )


async def _load_link(db: AsyncSession, link_id: UUID, user: User, *, lock: bool = False) -> ShortLink:
    statement = (
        select(ShortLink)
        .where(ShortLink.id == link_id, _visible_links(user))
        .options(selectinload(ShortLink.target_urls), selectinload(ShortLink.policy))
        .execution_options(populate_existing=True)
    )
    if lock:
        statement = statement.with_for_update()
    link = await db.scalar(statement)
    if link is None:
        raise NotFoundError("短链")
    return link


async def _ensure_manage_access(db: AsyncSession, user: User, domain_id: UUID) -> None:
    await ensure_domain_access(db, user, domain_id, AccessLevel.MANAGE)


async def _assign_code(
    db: AsyncSession,
    payload: ShortLinkWrite,
    *,
    exclude_link_id: UUID | None = None,
) -> tuple[str, bool]:
    if payload.custom_alias is None:
        return await create_unique_short_code(db, payload.domain_id), False
    try:
        code = await create_unique_short_code(
            db, payload.domain_id, payload.custom_alias, exclude_link_id=exclude_link_id
        )
    except ValueError as exc:
        if str(exc) == "Custom alias already exists in this domain":
            raise ConflictError("该短码在当前域名下已存在", ErrorCode.SHORT_CODE_CONFLICT) from exc
        raise
    return code, True


async def _replace_destinations(db: AsyncSession, link: ShortLink, payload: ShortLinkWrite) -> None:
    existing = await db.execute(
        select(TargetUrl).where(TargetUrl.short_link_id == link.id).with_for_update()
    )
    existing_by_id = {destination.id: destination for destination in existing.scalars()}
    supplied_ids = {destination.id for destination in payload.destinations if destination.id is not None}
    unknown_ids = supplied_ids.difference(existing_by_id)
    if unknown_ids:
        raise NotFoundError("目标 URL")

    for destination in payload.destinations:
        if destination.id is None:
            db.add(
                TargetUrl(
                    short_link_id=link.id,
                    url=destination.url,
                    url_type=destination.type,
                    weight=destination.weight,
                    is_active=destination.is_active,
                )
            )
        else:
            row = existing_by_id[destination.id]
            row.url = destination.url
            row.url_type = destination.type
            row.weight = destination.weight
            row.is_active = destination.is_active

    removed_ids = set(existing_by_id).difference(supplied_ids)
    if removed_ids:
        await db.execute(delete(TargetUrl).where(TargetUrl.id.in_(removed_ids)))


def _upsert_policy(
    link: ShortLink, payload: ShortLinkWrite, existing_policy: LinkPolicy | None
) -> LinkPolicy:
    policy = existing_policy or LinkPolicy(short_link_id=link.id)
    for field, value in payload.policy.model_dump().items():
        setattr(policy, field, value)
    return policy


@router.get("", response_model=Page[ShortLinkListItem])
async def list_short_links(
    domain_id: UUID | None = None,
    keyword: str | None = Query(default=None, max_length=4000),
    is_active: bool | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    filters = [_visible_links(current_user)]
    if domain_id is not None:
        filters.append(ShortLink.domain_id == domain_id)
    if keyword:
        term = f"%{keyword.strip()}%"
        filters.append(or_(ShortLink.short_code.ilike(term), ShortLink.note.ilike(term)))
    if is_active is not None:
        filters.append(ShortLink.is_active.is_(is_active))

    total = await db.scalar(select(func.count()).select_from(ShortLink).where(*filters)) or 0
    result = await db.execute(
        select(ShortLink)
        .where(*filters)
        .order_by(ShortLink.created_at.desc(), ShortLink.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    return Page(
        items=[_list_item(link) for link in result.scalars()],
        page=page,
        page_size=page_size,
        total=total,
    )


@router.get("/{link_id}", response_model=ShortLinkResponse)
async def get_short_link(
    link_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return _response(await _load_link(db, link_id, current_user))


@router.post("", response_model=ShortLinkResponse, status_code=status.HTTP_201_CREATED)
async def create_short_link(
    payload: ShortLinkWrite,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await db.commit()
    try:
        async with db.begin():
            await _ensure_manage_access(db, current_user, payload.domain_id)
            short_code, is_custom_alias = await _assign_code(db, payload)
            link = ShortLink(
                domain_id=payload.domain_id,
                short_code=short_code,
                is_custom_alias=is_custom_alias,
                note=payload.note,
                is_active=payload.is_active,
                owner_id=current_user.id,
            )
            db.add(link)
            await db.flush()
            await _replace_destinations(db, link, payload)
            db.add(_upsert_policy(link, payload, None))
            await db.flush()
        return _response(await _load_link(db, link.id, current_user))
    except IntegrityError as exc:
        if _is_short_code_unique_violation(exc):
            raise ConflictError("该短码在当前域名下已存在", ErrorCode.SHORT_CODE_CONFLICT) from exc
        raise


@router.put("/{link_id}", response_model=ShortLinkResponse)
async def update_short_link(
    link_id: UUID,
    payload: ShortLinkWrite,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await db.commit()
    try:
        async with db.begin():
            link = await _load_link(db, link_id, current_user, lock=True)
            await _ensure_manage_access(db, current_user, link.domain_id)
            if payload.domain_id != link.domain_id:
                raise ConflictError("短链域名不可修改")
            if payload.custom_alias is not None:
                link.short_code, link.is_custom_alias = await _assign_code(
                    db, payload, exclude_link_id=link.id
                )
            link.note = payload.note
            link.is_active = payload.is_active
            await _replace_destinations(db, link, payload)
            if link.policy is None:
                link.policy = _upsert_policy(link, payload, None)
            else:
                _upsert_policy(link, payload, link.policy)
            await db.flush()
        return _response(await _load_link(db, link_id, current_user))
    except IntegrityError as exc:
        if _is_short_code_unique_violation(exc):
            raise ConflictError("该短码在当前域名下已存在", ErrorCode.SHORT_CODE_CONFLICT) from exc
        raise


@router.delete("/{link_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_short_link(
    link_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Response:
    await db.commit()
    async with db.begin():
        link = await _load_link(db, link_id, current_user, lock=True)
        await _ensure_manage_access(db, current_user, link.domain_id)
        await db.delete(link)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
