from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import APIError, ConflictError, ErrorCode, NotFoundError
from app.core.security import get_password_hash
from app.db.session import get_db
from app.db.models import Domain, User, UserDomainAccess, UserRole
from app.dependencies import require_admin
from app.schemas.common import Page
from app.schemas.user import DomainGrantResponse, UserCreate, UserResponse, UserUpdate


router = APIRouter(prefix="/api/users", tags=["users"])


async def _grants_for_user(db: AsyncSession, user_id: UUID) -> list[DomainGrantResponse]:
    result = await db.execute(
        select(UserDomainAccess)
        .where(UserDomainAccess.user_id == user_id)
        .order_by(UserDomainAccess.domain_id)
    )
    return [
        DomainGrantResponse(domain_id=grant.domain_id, access_level=grant.access_level)
        for grant in result.scalars()
    ]


async def _grants_for_users(
    db: AsyncSession, user_ids: list[UUID]
) -> dict[UUID, list[DomainGrantResponse]]:
    grants_by_user = {user_id: [] for user_id in user_ids}
    if not user_ids:
        return grants_by_user
    result = await db.execute(
        select(UserDomainAccess)
        .where(UserDomainAccess.user_id.in_(user_ids))
        .order_by(UserDomainAccess.user_id, UserDomainAccess.domain_id)
    )
    for grant in result.scalars():
        grants_by_user[grant.user_id].append(
            DomainGrantResponse(domain_id=grant.domain_id, access_level=grant.access_level)
        )
    return grants_by_user


async def _user_response(
    db: AsyncSession, user: User, *, domain_access: list[DomainGrantResponse] | None = None
) -> UserResponse:
    return UserResponse(
        id=user.id,
        username=user.username,
        role=user.role,
        is_active=user.is_active,
        created_at=user.created_at,
        updated_at=user.updated_at,
        domain_access=(
            []
            if user.role == UserRole.ADMIN
            else domain_access if domain_access is not None else await _grants_for_user(db, user.id)
        ),
    )


async def _require_domains(db: AsyncSession, domain_ids: set[UUID]) -> None:
    if not domain_ids:
        return
    result = await db.execute(select(Domain.id).where(Domain.id.in_(domain_ids)))
    if set(result.scalars()) != domain_ids:
        raise NotFoundError("指定域名")


async def _assert_not_last_active_admin(db: AsyncSession, user: User, payload: UserUpdate) -> None:
    if user.role != UserRole.ADMIN or not user.is_active:
        return
    becomes_non_admin = payload.role is not None and payload.role != UserRole.ADMIN
    becomes_inactive = payload.is_active is False
    if not (becomes_non_admin or becomes_inactive):
        return
    active_admins = await db.scalar(
        select(func.count()).select_from(User).where(User.role == UserRole.ADMIN, User.is_active.is_(True))
    )
    if active_admins == 1:
        raise ConflictError("至少需要保留一名启用的管理员", ErrorCode.LAST_ADMIN_REQUIRED)


@router.get("", response_model=Page[UserResponse])
async def list_users(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    total = await db.scalar(select(func.count()).select_from(User)) or 0
    result = await db.execute(
        select(User).order_by(User.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    )
    users = result.scalars().all()
    grants_by_user = await _grants_for_users(
        db, [user.id for user in users if user.role != UserRole.ADMIN]
    )
    return Page(
        items=[
            await _user_response(db, user, domain_access=grants_by_user.get(user.id, []))
            for user in users
        ],
        page=page,
        page_size=page_size,
        total=total,
    )


@router.post("", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def create_user(
    payload: UserCreate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    await db.commit()
    async with db.begin():
        await _require_domains(db, {grant.domain_id for grant in payload.domain_access})

        user = User(
            username=payload.username,
            password_hash=get_password_hash(payload.password),
            role=payload.role,
            is_active=True if payload.is_active is None else payload.is_active,
        )
        db.add(user)
        await db.flush()
        if user.role != UserRole.ADMIN:
            for grant in payload.domain_access:
                db.add(
                    UserDomainAccess(
                        user_id=user.id, domain_id=grant.domain_id, access_level=grant.access_level
                    )
                )
        await db.flush()
        response = await _user_response(db, user)
    return response


@router.put("/{user_id}", response_model=UserResponse)
async def update_user(
    user_id: UUID,
    payload: UserUpdate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    await db.commit()
    async with db.begin():
        # Every administrator-removal transaction locks the same active-admin
        # set in the same order before it counts or changes a role. This
        # serializes concurrent deactivations/demotions of different admins.
        active_admins = await db.execute(
            select(User)
            .where(User.role == UserRole.ADMIN, User.is_active.is_(True))
            .order_by(User.id)
            .with_for_update()
        )
        active_admins.scalars().all()
        user = await db.scalar(select(User).where(User.id == user_id).with_for_update())
        if not user:
            raise NotFoundError("用户")
        await _assert_not_last_active_admin(db, user, payload)

        next_role = payload.role if payload.role is not None else user.role
        if next_role == UserRole.ADMIN and payload.domain_access:
            raise APIError(
                ErrorCode.VALIDATION_ERROR,
                "管理员不能设置域名权限",
                status.HTTP_422_UNPROCESSABLE_ENTITY,
            )
        if user.role == UserRole.ADMIN and next_role == UserRole.SUBACCOUNT and payload.domain_access is None:
            raise APIError(
                ErrorCode.VALIDATION_ERROR,
                "管理员降级为子账户时必须提交域名权限",
                status.HTTP_422_UNPROCESSABLE_ENTITY,
            )

        if payload.username is not None and payload.username != user.username:
            user.username = payload.username
        if payload.password is not None:
            user.password_hash = get_password_hash(payload.password)
        if payload.role is not None:
            user.role = payload.role
        if payload.is_active is not None:
            user.is_active = payload.is_active

        if next_role == UserRole.ADMIN:
            await db.execute(delete(UserDomainAccess).where(UserDomainAccess.user_id == user.id))
        elif payload.domain_access is not None:
            await _require_domains(db, {grant.domain_id for grant in payload.domain_access})
            await db.execute(delete(UserDomainAccess).where(UserDomainAccess.user_id == user.id))
            for grant in payload.domain_access:
                db.add(
                    UserDomainAccess(
                        user_id=user.id, domain_id=grant.domain_id, access_level=grant.access_level
                    )
                )
        await db.flush()
        response = await _user_response(db, user)
    return response
