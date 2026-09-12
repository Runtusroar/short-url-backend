from uuid import uuid4

import pytest
from starlette.requests import Request

import app.dependencies as dependencies
from app.core.config import settings
from app.core.errors import PermissionDeniedError
from app.core.security import create_access_token, decode_token, get_password_hash, verify_password
from app.db.models import User
from app.dependencies import require_admin


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


def test_final_dependencies_do_not_expose_legacy_role_helpers():
    assert not hasattr(dependencies, "require_role")
    assert not hasattr(dependencies, "require_staff")
    assert {"client_ip_identifier", "rate_limit", "user_identifier"} <= set(dependencies.__all__)


def test_require_admin_rejects_obsolete_operator_role():
    operator = User(username="operator", password_hash="hash", role="operator")

    with pytest.raises(PermissionDeniedError):
        require_admin(operator)


def _request(*, headers: dict[str, str] | None = None, client: tuple[str, int] | None = ("127.0.0.1", 1234)) -> Request:
    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/CampaignA",
        "raw_path": b"/CampaignA",
        "query_string": b"",
        "headers": [
            (name.lower().encode("latin-1"), value.encode("latin-1"))
            for name, value in (headers or {}).items()
        ],
    }
    if client is not None:
        scope["client"] = client
    return Request(scope)


@pytest.mark.asyncio
@pytest.mark.parametrize("transport", ["bearer", "cookie"])
async def test_authenticated_rate_limit_identifier_uses_the_same_user_for_bearer_and_cookie(transport):
    user_id = uuid4()
    token = create_access_token({"sub": str(user_id)})
    headers = (
        {"Authorization": f"Bearer {token}"}
        if transport == "bearer"
        else {"Cookie": f"access_token={token}"}
    )

    assert await dependencies.user_identifier(_request(headers=headers)) == f"user:{user_id}"


@pytest.mark.parametrize(
    ("trusted", "headers", "client", "expected"),
    [
        (
            True,
            {"X-Real-IP": "203.0.113.24", "X-Forwarded-For": "198.51.100.4, 10.0.0.1"},
            ("172.25.0.1", 1234),
            "203.0.113.24",
        ),
        (
            True,
            {"X-Real-IP": "invalid", "X-Forwarded-For": "198.51.100.4, 10.0.0.1"},
            ("172.25.0.1", 1234),
            "198.51.100.4",
        ),
        (
            False,
            {"X-Real-IP": "203.0.113.24", "X-Forwarded-For": "198.51.100.4"},
            ("172.25.0.1", 1234),
            "172.25.0.1",
        ),
        (True, {"X-Real-IP": "invalid", "X-Forwarded-For": "also-invalid"}, None, "unknown"),
        (False, {}, ("not-an-ip", 1234), "unknown"),
        (False, {}, None, "unknown"),
    ],
)
def test_redirect_rate_identifier_reuses_canonical_trusted_client_ip_semantics(
    monkeypatch, trusted, headers, client, expected
):
    monkeypatch.setattr(settings, "trust_proxy_headers", trusted)

    assert dependencies.client_ip_identifier(_request(headers=headers, client=client)) == expected
