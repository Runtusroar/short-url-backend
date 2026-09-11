from fastapi import Depends, Request
from fastapi_limiter.depends import RateLimiter

from app.core.config import settings
from app.core.security import decode_token


async def _noop_rate_limit():
    return None


def default_identifier(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


async def user_identifier(request: Request) -> str:
    auth = request.headers.get("authorization")
    if auth and auth.lower().startswith("bearer "):
        try:
            user_id = decode_token(auth[7:]).get("sub")
            if user_id:
                return f"user:{user_id}"
        except Exception:
            pass
    return default_identifier(request)


def rate_limit(times: int, seconds: int, identifier=None):
    if settings.redis_url:
        return Depends(RateLimiter(times=times, seconds=seconds, identifier=identifier))
    return Depends(_noop_rate_limit)
