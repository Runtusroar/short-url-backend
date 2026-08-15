from contextlib import asynccontextmanager
import logging

import redis.asyncio as redis
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi_limiter import FastAPILimiter
from sqlalchemy import text

from app.auth import decode_token
from app.config import settings
from app.core.client_ip import get_client_ip
from app.database import AsyncSessionLocal
from app.exceptions import register_exception_handlers
from app.rate_limit import rate_limit
from app.routers import admin, auth, domains, ip_blacklist, logs, redirect, short_links

logging.basicConfig(level=settings.log_level)
logger = logging.getLogger(__name__)


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
    return get_client_ip(request)


async def _ip_identifier(request: Request) -> str:
    return get_client_ip(request)


@asynccontextmanager
async def lifespan(app: FastAPI):
    if settings.redis_url:
        r = redis.from_url(settings.redis_url, encoding="utf-8", decode_responses=True)
        await FastAPILimiter.init(r)
        app.state.redis = r
    else:
        r = None
        app.state.redis = None
    yield
    if r:
        await r.aclose()
    app.state.redis = None


app = FastAPI(title="Short URL Service", lifespan=lifespan)
register_exception_handlers(app)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

ip_rate_limit = rate_limit(times=60, seconds=60, identifier=_ip_identifier)
user_rate_limit = rate_limit(times=120, seconds=60, identifier=_user_identifier)

app.include_router(auth.router)
app.include_router(short_links.router, dependencies=[user_rate_limit])
app.include_router(logs.router, dependencies=[user_rate_limit])
app.include_router(admin.router, dependencies=[user_rate_limit])
app.include_router(ip_blacklist.router, dependencies=[user_rate_limit])
app.include_router(domains.router, dependencies=[user_rate_limit])


@app.get("/health/live")
@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/health/ready")
async def readiness(request: Request):
    checks = {}
    try:
        async with AsyncSessionLocal() as session:
            await session.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception:
        logger.exception("Database readiness check failed")
        checks["database"] = "error"

    redis_client = request.app.state.redis
    if redis_client is None:
        checks["redis"] = "disabled"
    else:
        try:
            await redis_client.ping()
            checks["redis"] = "ok"
        except Exception:
            logger.exception("Redis readiness check failed")
            checks["redis"] = "error"

    if "error" in checks.values():
        return JSONResponse(
            status_code=503,
            content={"status": "not_ready", "checks": checks},
        )
    return {"status": "ready", "checks": checks}


app.include_router(redirect.router, dependencies=[ip_rate_limit])
