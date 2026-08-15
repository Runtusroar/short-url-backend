import os

os.environ.setdefault("REDIS_URL", "")
os.environ.setdefault(
    "DATABASE_URL", "postgresql+psycopg://shorturl:shorturl@localhost:5432/shorturl_test"
)

import pytest
import pytest_asyncio
from asgi_lifespan import LifespanManager
from httpx import AsyncClient

from app.main import app


@pytest_asyncio.fixture
async def client():
    async with LifespanManager(app):
        async with AsyncClient(app=app, base_url="http://test") as ac:
            yield ac
