from contextlib import asynccontextmanager

import redis.asyncio as redis
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi_limiter import FastAPILimiter

from app.api import (
    auth,
    dashboard,
    domains as domain_api,
    logs as access_logs,
    redirect as redirect_api,
    security,
    short_links,
    users,
)
from app.core.config import settings
from app.core.errors import register_exception_handlers
from app.dependencies import client_ip_identifier, rate_limit, user_identifier
from app.services.proxy import close_proxy_provider


@asynccontextmanager
async def lifespan(app: FastAPI):
    if settings.redis_url:
        r = redis.from_url(settings.redis_url, encoding="utf-8", decode_responses=True)
        await FastAPILimiter.init(r)
    else:
        r = None
    try:
        yield
    finally:
        try:
            if r:
                await r.aclose()
        finally:
            await close_proxy_provider()


app = FastAPI(title="Short URL Service", lifespan=lifespan)
register_exception_handlers(app)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

ip_rate_limit = rate_limit(times=60, seconds=60, identifier=client_ip_identifier)
user_rate_limit = rate_limit(times=120, seconds=60, identifier=user_identifier)

app.include_router(auth.router)
app.include_router(users.router, dependencies=[user_rate_limit])
app.include_router(domain_api.router, dependencies=[user_rate_limit])
app.include_router(security.router, dependencies=[user_rate_limit])
app.include_router(short_links.router, dependencies=[user_rate_limit])
app.include_router(dashboard.router, dependencies=[user_rate_limit])
app.include_router(access_logs.router, dependencies=[user_rate_limit])


@app.get("/health")
async def health():
    return {"status": "ok"}


app.include_router(redirect_api.router, dependencies=[ip_rate_limit])
