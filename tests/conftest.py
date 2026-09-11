import os
import uuid

os.environ.setdefault("REDIS_URL", "")
os.environ.setdefault(
    "DATABASE_URL", "postgresql+psycopg://shorturl:shorturl@localhost:18543/shorturl_test"
)

import pytest
import pytest_asyncio
from asgi_lifespan import LifespanManager
from httpx import AsyncClient, ASGITransport
from sqlalchemy import create_engine, select, text

from app.auth import get_password_hash
from app.config import settings
from app.database import Base
from app.main import app
from app.models import Domain, User
from app.db import AsyncSessionLocal

_sync_url = settings.database_url.replace("+asyncpg", "+psycopg")
_sync_engine = create_engine(_sync_url)


def _insert_user(conn, user_id: uuid.UUID, username: str, password: str, role: str):
    conn.execute(
        text(
            """
            INSERT INTO users (id, username, password_hash, role, is_active, created_at, updated_at)
            VALUES (:id, :username, :hash, :role, true, now(), now())
            """
        ),
        {
            "id": str(user_id),
            "username": username,
            "hash": get_password_hash(password),
            "role": role,
        },
    )


@pytest.fixture(scope="session", autouse=True)
def setup_database():
    """Create a fresh test schema and seed base accounts."""
    Base.metadata.drop_all(_sync_engine)
    Base.metadata.create_all(_sync_engine)

    admin_id = uuid.uuid4()
    operator_id = uuid.uuid4()
    client_id = uuid.uuid4()
    reader_id = uuid.uuid4()
    manager_id = uuid.uuid4()
    domain_a_id = uuid.uuid4()
    domain_b_id = uuid.uuid4()

    with _sync_engine.connect() as conn:
        _insert_user(conn, admin_id, "admin", "admin123", "admin")
        _insert_user(conn, operator_id, "operator", "operator123", "subaccount")
        _insert_user(conn, client_id, "client", "client123", "subaccount")
        _insert_user(conn, reader_id, "reader", "reader123", "subaccount")
        _insert_user(conn, manager_id, "manager", "manager123", "subaccount")

        conn.execute(
            text(
                """
                INSERT INTO domains (id, name, is_active, created_at)
                VALUES (:id, :name, true, now())
            """
        ),
            {"id": str(domain_a_id), "name": "test.local"},
        )

        conn.execute(
            text(
                """
                INSERT INTO domains (id, name, is_active, created_at)
                VALUES (:id, :name, true, now())
                """
            ),
            {"id": str(domain_b_id), "name": "second.test.local"},
        )

        for user_id in (operator_id, client_id):
            conn.execute(
                text(
                    """
                    INSERT INTO user_domain_access (user_id, domain_id, access_level, created_at)
                    VALUES (:user_id, :domain_id, 'manage', now())
                    """
                ),
                {
                    "user_id": str(user_id),
                    "domain_id": str(domain_a_id),
                },
            )

        for user_id, domain_id, access_level in (
            (reader_id, domain_a_id, "read"),
            (manager_id, domain_b_id, "manage"),
        ):
            conn.execute(
                text(
                    """
                    INSERT INTO user_domain_access (user_id, domain_id, access_level, created_at)
                    VALUES (:user_id, :domain_id, :access_level, now())
                    """
                ),
                {
                    "user_id": str(user_id),
                    "domain_id": str(domain_id),
                    "access_level": access_level,
                },
            )

        conn.commit()

    yield

    Base.metadata.drop_all(_sync_engine)


@pytest_asyncio.fixture
async def client():
    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac


@pytest_asyncio.fixture
async def db():
    async with AsyncSessionLocal() as session:
        transaction = await session.begin()
        try:
            yield session
        finally:
            await transaction.rollback()


@pytest_asyncio.fixture
async def make_user(db):
    async def factory(*, role: str, username: str | None = None) -> User:
        user = User(
            username=username or f"user-{uuid.uuid4().hex}",
            password_hash=get_password_hash("password123"),
            role=role,
        )
        db.add(user)
        await db.flush()
        return user

    return factory


@pytest_asyncio.fixture
async def make_domain(db):
    async def factory(*, is_active: bool = True) -> Domain:
        domain = Domain(name=f"{uuid.uuid4().hex}.test.local", is_active=is_active)
        db.add(domain)
        await db.flush()
        return domain

    return factory


@pytest_asyncio.fixture
async def domain_a(db):
    return await db.scalar(select(Domain).where(Domain.name == "test.local"))


@pytest_asyncio.fixture
async def domain_b(db):
    return await db.scalar(select(Domain).where(Domain.name == "second.test.local"))


async def _login(client: AsyncClient, username: str, password: str) -> str:
    resp = await client.post(
        "/api/auth/login",
        data={"username": username, "password": password},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


@pytest_asyncio.fixture
async def admin_token(client):
    return await _login(client, "admin", "admin123")


@pytest_asyncio.fixture
async def operator_token(client):
    return await _login(client, "operator", "operator123")


@pytest_asyncio.fixture
async def client_token(client):
    return await _login(client, "client", "client123")


@pytest_asyncio.fixture
async def read_token(client):
    return await _login(client, "reader", "reader123")


@pytest_asyncio.fixture
async def manage_token(client):
    return await _login(client, "manager", "manager123")


@pytest_asyncio.fixture
async def default_domain(client, admin_token):
    resp = await client.get(
        "/api/domains",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    domains = resp.json()
    assert domains, "test domain not found"
    return domains[0]
