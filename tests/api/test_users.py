import uuid

import pytest


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
