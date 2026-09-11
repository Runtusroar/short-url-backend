from uuid import UUID

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import decode_token, oauth2_scheme
from app.database import get_db
from app.db.models import User, UserRole
from app.exceptions import PermissionDeniedError


def _extract_token(request: Request, header_token: str | None) -> str | None:
    if header_token:
        return header_token
    auth = request.headers.get("authorization")
    if auth and auth.lower().startswith("bearer "):
        return auth[7:]
    return request.cookies.get("access_token")


async def get_current_user(
    request: Request,
    header_token: str | None = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    token = _extract_token(request, header_token)
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

    payload = decode_token(token)
    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    try:
        parsed_user_id = UUID(str(user_id))
    except (AttributeError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token") from exc

    user = await db.scalar(select(User).where(User.id == parsed_user_id))
    if not user or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found or inactive")
    return user


def require_role(*roles: str):
    def checker(current_user: User = Depends(get_current_user)) -> User:
        if current_user.role not in roles:
            raise PermissionDeniedError("权限不足")
        return current_user

    return checker


def require_admin(current_user: User = Depends(get_current_user)) -> User:
    if current_user.role != UserRole.ADMIN:
        raise PermissionDeniedError("需要管理员权限")
    return current_user


require_staff = require_role("admin", "operator")

__all__ = ["get_db", "get_current_user", "require_role", "require_admin", "require_staff"]
