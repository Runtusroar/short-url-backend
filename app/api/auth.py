from fastapi import APIRouter, Depends, Response
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import PermissionDeniedError, UnauthorizedError
from app.core.security import create_access_token, verify_password
from app.db.session import get_db
from app.db.models import User, UserDomainAccess, UserRole
from app.dependencies import client_ip_identifier, get_current_user, rate_limit
from app.schemas.auth import Token
from app.schemas.user import DomainGrantResponse, UserResponse

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _issue_token(user: User) -> str:
    return create_access_token({"sub": str(user.id)})


@router.post(
    "/login",
    response_model=Token,
    dependencies=[rate_limit(times=60, seconds=60, identifier=client_ip_identifier)],
)
async def login(
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: AsyncSession = Depends(get_db),
):
    user = await db.scalar(select(User).where(User.username == form_data.username))
    if not user or not verify_password(form_data.password, user.password_hash):
        raise UnauthorizedError("用户名或密码错误")
    if not user.is_active:
        raise PermissionDeniedError("用户已停用")
    return {"access_token": _issue_token(user)}


@router.post(
    "/login-cookie",
    response_model=UserResponse,
    dependencies=[rate_limit(times=60, seconds=60, identifier=client_ip_identifier)],
)
async def login_cookie(
    response: Response,
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: AsyncSession = Depends(get_db),
):
    user = await db.scalar(select(User).where(User.username == form_data.username))
    if not user or not verify_password(form_data.password, user.password_hash):
        raise UnauthorizedError("用户名或密码错误")
    if not user.is_active:
        raise PermissionDeniedError("用户已停用")

    token = _issue_token(user)
    response.set_cookie(
        key="access_token",
        value=token,
        httponly=True,
        max_age=settings.access_token_expire_minutes * 60,
        samesite="lax",
        secure=settings.cookie_secure,
    )
    return user


@router.post("/logout")
async def logout(response: Response):
    response.delete_cookie(key="access_token")
    return {"detail": "Logged out"}


@router.get("/me", response_model=UserResponse)
async def me(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if current_user.role == UserRole.ADMIN:
        return UserResponse(
            id=current_user.id,
            username=current_user.username,
            role=current_user.role,
            is_active=current_user.is_active,
            created_at=current_user.created_at,
            updated_at=current_user.updated_at,
            domain_access=[],
        )

    result = await db.execute(
        select(UserDomainAccess)
        .where(UserDomainAccess.user_id == current_user.id)
        .order_by(UserDomainAccess.domain_id)
    )
    return UserResponse(
        id=current_user.id,
        username=current_user.username,
        role=current_user.role,
        is_active=current_user.is_active,
        created_at=current_user.created_at,
        updated_at=current_user.updated_at,
        domain_access=[
            DomainGrantResponse(domain_id=grant.domain_id, access_level=grant.access_level)
            for grant in result.scalars()
        ],
    )
