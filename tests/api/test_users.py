import asyncio
import uuid
from uuid import UUID

import pytest
from sqlalchemy import event, func, select, update

from app.api import users as users_api
from app.core.errors import ConflictError
from app.db import AsyncSessionLocal, engine
from app.db.models import User, UserRole
from app.schemas.user import UserUpdate


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_unmatched_routes_use_the_common_error_envelope(client):
    response = await client.get("/api/not-a-route")

    assert response.status_code == 404
    assert response.json() == {"code": "HTTP_404", "message": "Not Found", "details": None}


@pytest.mark.asyncio
async def test_admin_can_create_list_update_and_deactivate_user_with_replaced_grants(
    client, admin_token, domain_a, domain_b
):
    username = f"reader-{uuid.uuid4().hex[:10]}"
    created = await client.post(
        "/api/users",
        headers=auth(admin_token),
        json={
            "username": username,
            "password": "reader-pass",
            "role": "subaccount",
            "domain_access": [{"domain_id": str(domain_a.id), "access_level": "read"}],
        },
    )

    assert created.status_code == 201, created.text
    user = created.json()
    assert user["domain_access"] == [{"domain_id": str(domain_a.id), "access_level": "read"}]

    listed = await client.get("/api/users?page=1&page_size=10", headers=auth(admin_token))
    assert listed.status_code == 200, listed.text
    assert listed.json()["total"] >= 6
    assert any(item["id"] == user["id"] for item in listed.json()["items"])

    updated = await client.put(
        f"/api/users/{user['id']}",
        headers=auth(admin_token),
        json={
            "domain_access": [{"domain_id": str(domain_b.id), "access_level": "manage"}],
            "is_active": False,
        },
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["is_active"] is False
    assert updated.json()["domain_access"] == [
        {"domain_id": str(domain_b.id), "access_level": "manage"}
    ]


@pytest.mark.asyncio
async def test_user_update_without_password_keeps_existing_password(client, admin_token, domain_a):
    username = f"password-{uuid.uuid4().hex[:10]}"
    created = await client.post(
        "/api/users",
        headers=auth(admin_token),
        json={
            "username": username,
            "password": "original-pass",
            "role": "subaccount",
            "domain_access": [{"domain_id": str(domain_a.id), "access_level": "read"}],
        },
    )
    assert created.status_code == 201, created.text

    updated = await client.put(
        f"/api/users/{created.json()['id']}",
        headers=auth(admin_token),
        json={"username": f"renamed-{uuid.uuid4().hex[:10]}"},
    )
    assert updated.status_code == 200, updated.text

    login = await client.post(
        "/api/auth/login",
        data={"username": updated.json()["username"], "password": "original-pass"},
    )
    assert login.status_code == 200, login.text


@pytest.mark.asyncio
async def test_concurrent_usernames_return_a_named_conflict_and_leave_the_admin_token_usable(
    client, admin_token
):
    """The unique index, not a racy preflight read, is authoritative for concurrent creates."""
    username = f"concurrent-{uuid.uuid4().hex[:12]}"
    payload = {"username": username, "password": "reader-pass", "role": "subaccount"}
    first, second = await asyncio.gather(
        client.post("/api/users", headers=auth(admin_token), json=payload),
        client.post("/api/users", headers=auth(admin_token), json=payload),
    )

    assert sorted(response.status_code for response in (first, second)) == [201, 409]
    conflict = next(response for response in (first, second) if response.status_code == 409)
    assert conflict.json()["code"] == "USERNAME_CONFLICT"
    still_authenticated = await client.get("/api/auth/me", headers=auth(admin_token))
    assert still_authenticated.status_code == 200
    assert still_authenticated.json()["username"] == "admin"


@pytest.mark.asyncio
async def test_user_administration_requires_an_authenticated_administrator(client, operator_token, domain_a):
    unauthenticated = await client.get("/api/users")
    assert unauthenticated.status_code == 401
    assert unauthenticated.json()["details"] is None

    denied = await client.post(
        "/api/users",
        headers=auth(operator_token),
        json={
            "username": f"denied-{uuid.uuid4().hex[:10]}",
            "password": "reader-pass",
            "role": "subaccount",
            "domain_access": [{"domain_id": str(domain_a.id), "access_level": "read"}],
        },
    )
    assert denied.status_code == 403
    assert denied.json()["code"] == "PERMISSION_DENIED"


@pytest.mark.asyncio
async def test_subaccounts_cannot_update_or_deactivate_users(client, admin_token, operator_token):
    created = await client.post(
        "/api/users",
        headers=auth(admin_token),
        json={
            "username": f"protected-{uuid.uuid4().hex[:10]}",
            "password": "reader-pass",
            "role": "subaccount",
            "domain_access": [],
        },
    )
    assert created.status_code == 201, created.text

    for payload in ({"username": f"changed-{uuid.uuid4().hex[:10]}"}, {"is_active": False}):
        denied = await client.put(
            f"/api/users/{created.json()['id']}", headers=auth(operator_token), json=payload
        )
        assert denied.status_code == 403
        assert denied.json()["code"] == "PERMISSION_DENIED"


@pytest.mark.asyncio
async def test_user_validation_missing_domain_duplicate_username_and_missing_user_are_explicit(
    client, admin_token
):
    invalid = await client.post(
        "/api/users",
        headers=auth(admin_token),
        json={"username": "ab", "password": "short", "role": "subaccount", "domain_access": []},
    )
    assert invalid.status_code == 422
    assert invalid.json()["code"] == "VALIDATION_ERROR"
    assert invalid.json()["details"]

    duplicate = await client.post(
        "/api/users",
        headers=auth(admin_token),
        json={"username": "admin", "password": "reader-pass", "role": "subaccount", "domain_access": []},
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["code"] == "USERNAME_CONFLICT"

    missing = await client.put(
        f"/api/users/{uuid.uuid4()}",
        headers=auth(admin_token),
        json={"is_active": False},
    )
    assert missing.status_code == 404
    assert missing.json()["code"] == "NOT_FOUND"


@pytest.mark.asyncio
async def test_cannot_deactivate_the_last_active_administrator(client, admin_token):
    users = await client.get("/api/users", headers=auth(admin_token))
    assert users.status_code == 200, users.text
    admin = next(item for item in users.json()["items"] if item["username"] == "admin")
    response = await client.put(
        f"/api/users/{admin['id']}", headers=auth(admin_token), json={"is_active": False}
    )

    assert response.status_code == 409
    assert response.json()["code"] == "LAST_ADMIN_REQUIRED"


@pytest.mark.asyncio
async def test_list_users_loads_all_domain_grants_in_one_query(client, admin_token):
    """Looking up grants per returned user would make page cost grow with page size."""
    grant_queries: list[str] = []

    def record(_, __, statement, ___, ____, _____):
        if "user_domain_access" in statement.lower():
            grant_queries.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", record)
    try:
        response = await client.get("/api/users?page=1&page_size=20", headers=auth(admin_token))
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", record)

    assert response.status_code == 200, response.text
    assert len(response.json()["items"]) > 1
    assert len(grant_queries) == 1


@pytest.mark.asyncio
async def test_concurrent_admin_removals_leave_one_active_administrator(client, admin_token):
    async def create_admin() -> dict[str, object]:
        response = await client.post(
            "/api/users",
            headers=auth(admin_token),
            json={
                "username": f"concurrent-admin-{uuid.uuid4().hex[:10]}",
                "password": "admin-pass",
                "role": "admin",
                "domain_access": [],
            },
        )
        assert response.status_code == 201, response.text
        return response.json()

    first_admin, second_admin = await create_admin(), await create_admin()
    async with AsyncSessionLocal() as session:
        seed_admin = await session.scalar(select(User).where(User.username == "admin"))
        assert seed_admin is not None
        seed_admin_id = seed_admin.id
        seed_admin.is_active = False
        await session.commit()

    barrier = asyncio.Barrier(2)

    async def remove_admin(user_id: str, payload: dict[str, object]) -> str:
        async with AsyncSessionLocal() as session:
            try:
                # Synchronize callers before either transaction acquires the row locks;
                # a generous timeout only protects the test process from a stalled task.
                await asyncio.wait_for(barrier.wait(), timeout=5)
                await users_api.update_user(UUID(user_id), UserUpdate(**payload), session, None)
            except ConflictError as exc:
                return exc.code
            return "UPDATED"

    try:
        results = await asyncio.gather(
            remove_admin(str(first_admin["id"]), {"is_active": False}),
            remove_admin(str(second_admin["id"]), {"role": "subaccount"}),
        )

        assert sorted(results) == ["LAST_ADMIN_REQUIRED", "UPDATED"]
        async with AsyncSessionLocal() as session:
            active_admins = await session.scalar(
                select(func.count())
                .select_from(User)
                .where(User.role == UserRole.ADMIN, User.is_active.is_(True))
            )
        assert active_admins == 1
    finally:
        async with AsyncSessionLocal() as session:
            await session.execute(update(User).where(User.id == seed_admin_id).values(is_active=True))
            await session.commit()
