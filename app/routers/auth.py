from fastapi import APIRouter, Depends, Response
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import create_access_token, verify_password
from app.config import settings
from app.database import get_db
from app.dependencies import get_current_user
from app.exceptions import PermissionDeniedError, UnauthorizedError
from app.models import User
from app.rate_limit import rate_limit
from app.schemas import Token, UserResponse

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _issue_token(user: User) -> str:
    return create_access_token({"sub": str(user.id)})


@router.post("/login", response_model=Token, dependencies=[rate_limit(times=60, seconds=60)])
async def login(
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(User).where(User.username == form_data.username))
    user = result.scalar_one_or_none()
    if not user or not verify_password(form_data.password, user.password_hash):
        raise UnauthorizedError("用户名或密码错误")
    if not user.is_active:
        raise PermissionDeniedError("用户已停用")
    token = _issue_token(user)
    return {"access_token": token}


@router.post("/login-cookie", response_model=UserResponse, dependencies=[rate_limit(times=60, seconds=60)])
async def login_cookie(
    response: Response,
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(User).where(User.username == form_data.username))
    user = result.scalar_one_or_none()
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
async def me(current_user: User = Depends(get_current_user)):
    return current_user
