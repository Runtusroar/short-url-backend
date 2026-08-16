import os
import uuid
from pathlib import Path

os.environ.setdefault("REDIS_URL", "")
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+psycopg://shorturl:shorturl@localhost:18543/shorturl_test",
)

import pytest
import pytest_asyncio
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, text

from app.core.config import settings
from app.core.database import Base
from app.core.security import get_password_hash
from app.main import app

_sync_url = settings.database_url.replace("+asyncpg", "+psycopg")
_sync_engine = create_engine(_sync_url)
_TESTS_ROOT = Path(__file__).resolve().parent
_MIGRATION_TESTS_ROOT = _TESTS_ROOT / "migrations"
_SCHEMA_CHECK_TEST = _TESTS_ROOT / "deployment" / "test_schema_check.py"


def _uses_disposable_schema_database(path: Path | str) -> bool:
    """Allow only exact migration/schema-check test paths to bypass shared DDL."""
    resolved = Path(path).resolve()
    return resolved == _SCHEMA_CHECK_TEST or (
        resolved.is_relative_to(_MIGRATION_TESTS_ROOT) and resolved.suffix == ".py"
    )


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


@pytest.fixture(scope="module", autouse=True)
def setup_database(request: pytest.FixtureRequest):
    """Create a fresh test schema and seed base accounts."""
    # Migration tests own isolated UUID-named databases and must never touch
    # the application's shared test database as an incidental autouse effect.
    if _uses_disposable_schema_database(request.path):
        yield
        return

    Base.metadata.drop_all(_sync_engine)
    Base.metadata.create_all(_sync_engine)

    admin_id = uuid.uuid4()
    operator_id = uuid.uuid4()
    client_id = uuid.uuid4()
    domain_id = uuid.uuid4()

    with _sync_engine.connect() as conn:
        _insert_user(conn, admin_id, "admin", "admin123", "admin")
        _insert_user(conn, operator_id, "operator", "operator123", "operator")
        _insert_user(conn, client_id, "client", "client123", "client")

        conn.execute(
            text(
                """
                INSERT INTO domains (id, name, is_active, is_default, created_at)
                VALUES (:id, 'test.local', true, true, now())
                """
            ),
            {"id": str(domain_id)},
        )

        for user_id in (operator_id, client_id):
            conn.execute(
                text(
                    """
                    INSERT INTO user_domains (id, user_id, domain_id, created_at)
                    VALUES (:id, :user_id, :domain_id, now())
                    """
                ),
                {
                    "id": str(uuid.uuid4()),
                    "user_id": str(user_id),
                    "domain_id": str(domain_id),
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
async def default_domain(client, admin_token):
    resp = await client.get(
        "/api/domains",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    domains = resp.json()
    default = [d for d in domains if d["is_default"]]
    assert default, "default domain not found"
    return default[0]
