from fastapi import APIRouter, Depends, Response
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.core.rate_limit import rate_limit
from app.core.security import get_current_user
from app.features.auth.schemas import Token
from app.features.auth.service import authenticate_user, issue_token
from app.features.users.schemas import UserResponse
from app.models import User

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/login", response_model=Token, dependencies=[rate_limit(times=60, seconds=60)])
async def login(
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: AsyncSession = Depends(get_db),
):
    user = await authenticate_user(db, form_data.username, form_data.password)
    return {"access_token": issue_token(user)}


@router.post("/login-cookie", response_model=UserResponse, dependencies=[rate_limit(times=60, seconds=60)])
async def login_cookie(
    response: Response,
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: AsyncSession = Depends(get_db),
):
    user = await authenticate_user(db, form_data.username, form_data.password)
    response.set_cookie(
        key="access_token",
        value=issue_token(user),
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
