from contextlib import asynccontextmanager

import redis.asyncio as redis
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi_limiter import FastAPILimiter

from app.auth import decode_token
from app.config import settings
from app.exceptions import register_exception_handlers
from app.rate_limit import rate_limit
from app.routers import admin, auth, domains, ip_blacklist, logs, redirect, short_links


def _default_identifier(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


async def _user_identifier(request: Request) -> str:
    auth = request.headers.get("authorization")
    if auth and auth.lower().startswith("bearer "):
        try:
            payload = decode_token(auth[7:])
            user_id = payload.get("sub")
            if user_id:
                return f"user:{user_id}"
        except Exception:
            pass
    return _default_identifier(request)


@asynccontextmanager
async def lifespan(app: FastAPI):
    if settings.redis_url:
        r = redis.from_url(settings.redis_url, encoding="utf-8", decode_responses=True)
        await FastAPILimiter.init(r)
    else:
        r = None
    yield
    if r:
        await r.aclose()


app = FastAPI(title="Short URL Service", lifespan=lifespan)
register_exception_handlers(app)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

ip_rate_limit = rate_limit(times=60, seconds=60)
user_rate_limit = rate_limit(times=120, seconds=60, identifier=_user_identifier)

app.include_router(auth.router)
app.include_router(short_links.router, dependencies=[user_rate_limit])
app.include_router(logs.router, dependencies=[user_rate_limit])
app.include_router(admin.router, dependencies=[user_rate_limit])
app.include_router(ip_blacklist.router, dependencies=[user_rate_limit])
app.include_router(domains.router, dependencies=[user_rate_limit])


@app.get("/health")
async def health():
    return {"status": "ok"}


app.include_router(redirect.router, dependencies=[ip_rate_limit])
