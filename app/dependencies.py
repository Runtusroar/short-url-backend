from uuid import UUID

from fastapi import Depends, HTTPException, Request, status
from fastapi_limiter.depends import RateLimiter
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import PermissionDeniedError
from app.core.security import decode_token, oauth2_scheme
from app.db.session import get_db as _get_db
from app.db.models import User, UserRole
from app.services.request_metadata import request_client_ip


async def _noop_rate_limit():
    return None


async def client_ip_identifier(request: Request) -> str:
    return request_client_ip(request) or "unknown"


def _extract_token(request: Request, header_token: str | None) -> str | None:
    if header_token:
        return header_token
    auth = request.headers.get("authorization")
    if auth and auth.lower().startswith("bearer "):
        return auth[7:]
    return request.cookies.get("access_token")


async def user_identifier(request: Request) -> str:
    if token := _extract_token(request, None):
        try:
            user_id = UUID(str(decode_token(token).get("sub")))
            return f"user:{user_id}"
        except Exception:
            pass
    return await client_ip_identifier(request)


def rate_limit(times: int, seconds: int, identifier=None):
    if settings.redis_url:
        return Depends(RateLimiter(times=times, seconds=seconds, identifier=identifier))
    return Depends(_noop_rate_limit)


async def get_current_user(
    request: Request,
    header_token: str | None = Depends(oauth2_scheme),
    db: AsyncSession = Depends(_get_db),
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


def require_admin(current_user: User = Depends(get_current_user)) -> User:
    if current_user.role != UserRole.ADMIN:
        raise PermissionDeniedError("需要管理员权限")
    return current_user


__all__ = [
    "client_ip_identifier",
    "get_current_user",
    "rate_limit",
    "require_admin",
    "user_identifier",
]
