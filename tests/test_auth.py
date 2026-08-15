import pytest

from app.core.security import create_access_token, decode_token, get_password_hash, verify_password


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


async def test_login_cookie_uses_configured_secure_flag(client, monkeypatch):
    monkeypatch.setattr("app.routers.auth.settings.cookie_secure", True)
    response = await client.post(
        "/api/auth/login-cookie",
        data={"username": "admin", "password": "admin123"},
    )
    assert response.status_code == 200
    assert "Secure" in response.headers["set-cookie"]
