import pytest

import app.dependencies as dependencies
from app.auth import create_access_token, decode_token, get_password_hash, verify_password
from app.core.config import settings
from app.db.models import User
from app.dependencies import require_admin
from app.exceptions import PermissionDeniedError


def test_password_hash():
    hashed = get_password_hash("secret")
    assert verify_password("secret", hashed) is True
    assert verify_password("wrong", hashed) is False


def test_create_and_decode_token():
    token = create_access_token({"sub": "user-123"})
    payload = decode_token(token)
    assert payload["sub"] == "user-123"


def test_decode_invalid_token():
    with pytest.raises(Exception):
        decode_token("not-a-token")


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


def test_final_dependencies_do_not_expose_legacy_role_helpers():
    assert dependencies.__all__ == ["get_current_user", "require_admin"]
    assert not hasattr(dependencies, "require_role")
    assert not hasattr(dependencies, "require_staff")


def test_require_admin_rejects_obsolete_operator_role():
    operator = User(username="operator", password_hash="hash", role="operator")

    with pytest.raises(PermissionDeniedError):
        require_admin(operator)
