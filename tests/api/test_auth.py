import pytest
from sqlalchemy import select

from app.core.config import settings
from app.core.security import create_access_token
from app.db.models import User
from app.db.session import AsyncSessionLocal


async def test_malformed_token_subject_is_unauthorized(client):
    token = create_access_token({"sub": "not-a-uuid"})

    response = await client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 401


@pytest.mark.parametrize("cookie_secure", [True, False])
async def test_login_cookie_uses_configured_security_attributes(client, monkeypatch, cookie_secure):
    monkeypatch.setattr(settings, "cookie_secure", cookie_secure)

    response = await client.post(
        "/api/auth/login-cookie",
        data={"username": "admin", "password": "admin123"},
    )

    cookie = response.headers["set-cookie"]
    assert response.status_code == 200
    assert ("Secure" in cookie) is cookie_secure
    assert "HttpOnly" in cookie
    assert "SameSite=lax" in cookie


async def test_logout_succeeds_and_clears_cookie(client):
    login = await client.post(
        "/api/auth/login-cookie",
        data={"username": "admin", "password": "admin123"},
    )
    assert login.status_code == 200

    response = await client.post("/api/auth/logout")

    assert response.status_code == 200
    assert response.json() == {"detail": "Logged out"}
    assert "access_token=\"\"" in response.headers["set-cookie"]


async def test_me_returns_the_authenticated_user(client, admin_token):
    response = await client.get("/api/auth/me", headers={"Authorization": f"Bearer {admin_token}"})

    assert response.status_code == 200
    assert response.json()["username"] == "admin"
    assert response.json()["role"] == "admin"


async def test_me_returns_the_current_subaccount_grants_and_an_explicit_empty_admin_contract(
    client, admin_token, operator_token, read_token, domain_a
):
    """The session bootstrap must expose the grants used for every subaccount authorization check."""
    operator = await client.get(
        "/api/auth/me", headers={"Authorization": f"Bearer {operator_token}"}
    )
    reader = await client.get("/api/auth/me", headers={"Authorization": f"Bearer {read_token}"})
    admin = await client.get("/api/auth/me", headers={"Authorization": f"Bearer {admin_token}"})

    assert operator.status_code == reader.status_code == admin.status_code == 200
    assert operator.json()["domain_access"] == [
        {"domain_id": str(domain_a.id), "access_level": "manage"}
    ]
    assert reader.json()["domain_access"] == [
        {"domain_id": str(domain_a.id), "access_level": "read"}
    ]
    assert admin.json()["domain_access"] == []


async def test_login_rejects_wrong_password(client):
    response = await client.post(
        "/api/auth/login",
        data={"username": "admin", "password": "wrong-password"},
    )

    assert response.status_code == 401
    assert response.json()["code"] == "UNAUTHORIZED"


async def test_disabled_user_cannot_log_in(client):
    async with AsyncSessionLocal() as session:
        user = await session.scalar(select(User).where(User.username == "admin"))
        assert user is not None
        user.is_active = False
        await session.commit()

    try:
        response = await client.post(
            "/api/auth/login",
            data={"username": "admin", "password": "admin123"},
        )
        assert response.status_code == 403
        assert response.json()["code"] == "PERMISSION_DENIED"
    finally:
        async with AsyncSessionLocal() as session:
            user = await session.scalar(select(User).where(User.username == "admin"))
            assert user is not None
            user.is_active = True
            await session.commit()
