import pytest

from app.auth import create_access_token, decode_token, get_password_hash, verify_password
from app.core.config import Settings, settings


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


async def test_login_cookie_uses_secure_environment_setting(client, monkeypatch):
    monkeypatch.setenv("COOKIE_SECURE", "true")
    monkeypatch.setattr(settings, "cookie_secure", Settings().cookie_secure)

    response = await client.post(
        "/api/auth/login-cookie",
        data={"username": "admin", "password": "admin123"},
    )

    cookie = response.headers["set-cookie"]
    assert response.status_code == 200
    assert "Secure" in cookie
    assert "HttpOnly" in cookie
    assert "SameSite=lax" in cookie
