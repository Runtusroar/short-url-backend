from contextlib import asynccontextmanager

import redis.asyncio as redis
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi_limiter import FastAPILimiter

from app.api import auth, domains as domain_api, security, short_links, users
from app.core.config import settings
from app.exceptions import register_exception_handlers
from app.rate_limit import rate_limit, user_identifier
from app.routers import logs, redirect


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
user_rate_limit = rate_limit(times=120, seconds=60, identifier=user_identifier)

app.include_router(auth.router)
app.include_router(users.router, dependencies=[user_rate_limit])
app.include_router(domain_api.router, dependencies=[user_rate_limit])
app.include_router(security.router, dependencies=[user_rate_limit])
app.include_router(short_links.router, dependencies=[user_rate_limit])
app.include_router(logs.router, dependencies=[user_rate_limit])


@app.get("/health")
async def health():
    return {"status": "ok"}


app.include_router(redirect.router, dependencies=[ip_rate_limit])
