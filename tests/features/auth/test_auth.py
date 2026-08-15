async def test_login_cookie_uses_configured_secure_flag(client, monkeypatch):
    monkeypatch.setattr("app.features.auth.router.settings.cookie_secure", True)
    response = await client.post(
        "/api/auth/login-cookie",
        data={"username": "admin", "password": "admin123"},
    )
    assert response.status_code == 200
    assert "Secure" in response.headers["set-cookie"]
