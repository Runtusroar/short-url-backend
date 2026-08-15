from fastapi import Depends, Request
from fastapi_limiter.depends import RateLimiter

from app.core.config import settings
from app.core.client_ip import get_client_ip


async def _noop_rate_limit():
    return None


async def _ip_identifier(request: Request) -> str:
    return get_client_ip(request)


def rate_limit(times: int, seconds: int, identifier=None):
    if settings.redis_url:
        limiter_identifier = _ip_identifier if identifier is None else identifier
        return Depends(
            RateLimiter(
                times=times,
                seconds=seconds,
                identifier=limiter_identifier,
            )
        )
    return Depends(_noop_rate_limit)
