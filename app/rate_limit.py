from fastapi import Depends
from fastapi_limiter.depends import RateLimiter

from app.config import settings


async def _noop_rate_limit():
    return None


def rate_limit(times: int, seconds: int, identifier=None):
    if settings.redis_url:
        return Depends(RateLimiter(times=times, seconds=seconds, identifier=identifier))
    return Depends(_noop_rate_limit)
