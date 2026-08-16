"""End-to-end API integration tests."""

import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError
from sqlalchemy import text

from app.core.config import settings
from app.core.security import get_password_hash
from app.features.short_links.schemas import ShortLinkCreate
from app.main import app
from tests.conftest import _sync_engine

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _host(domain: str = "test.local") -> dict:
    return {"Host": domain}


async def _create_short_link(client: AsyncClient, token: str, domain_id: str) -> dict:
    resp = await client.post(
        "/api/short-links",
        headers={**_auth(token), **_host()},
        json={"domain_id": domain_id, "name": "integration test link"},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _add_target_url(
    client: AsyncClient, token: str, link_id: str, url: str = "https://example.com"
) -> dict:
    resp = await client.post(
        f"/api/short-links/{link_id}/urls",
        headers={**_auth(token), **_host()},
        json={"url": url, "url_type": "allowed", "weight": 1, "is_active": True},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _add_allow_rule(client: AsyncClient, token: str, link_id: str) -> dict:
    resp = await client.post(
        f"/api/short-links/{link_id}/rules",
        headers={**_auth(token), **_host()},
        json={"name": "allow redirect", "action": "allow", "priority": 0},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------


async def test_login_success(client):
    resp = await client.post(
        "/api/auth/login",
        data={"username": "admin", "password": "admin123"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "access_token" in data
    assert data["token_type"] == "bearer"


async def test_login_wrong_password(client):
    resp = await client.post(
        "/api/auth/login",
        data={"username": "admin", "password": "wrong"},
    )
    assert resp.status_code == 401
    assert resp.json()["code"] == "UNAUTHORIZED"


async def test_short_legacy_username_can_log_in_after_normalization(client):
    with _sync_engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO users (id, username, password_hash, role, is_active, created_at, updated_at)
                VALUES (:id, 'xy', :password_hash, 'client', true, now(), now())
                """
            ),
            {
                "id": str(uuid.uuid4()),
                "password_hash": get_password_hash("legacy-password"),
            },
        )
    response = await client.post(
        "/api/auth/login",
        data={"username": " XY ", "password": "legacy-password"},
    )
    assert response.status_code == 200


async def test_short_legacy_username_is_returned_by_admin_user_list(
    client, admin_token
):
    with _sync_engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO users (id, username, password_hash, role, is_active, created_at, updated_at)
                VALUES (:id, 'xz', :password_hash, 'client', true, now(), now())
                """
            ),
            {
                "id": str(uuid.uuid4()),
                "password_hash": get_password_hash("legacy-password"),
            },
        )
    response = await client.get("/api/admin/users", headers=_auth(admin_token))
    assert response.status_code == 200
    assert any(user["username"] == "xz" for user in response.json())


async def test_me(client, admin_token):
    resp = await client.get("/api/auth/me", headers=_auth(admin_token))
    assert resp.status_code == 200
    data = resp.json()
    assert data["username"] == "admin"
    assert data["role"] == "admin"


async def test_protected_without_token(client):
    resp = await client.get("/api/domains")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Domains
# ---------------------------------------------------------------------------


async def test_admin_can_create_domain(client, admin_token):
    resp = await client.post(
        "/api/domains",
        headers=_auth(admin_token),
        json={"name": "example.com", "is_active": True, "is_default": False},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "example.com"


async def test_operator_cannot_create_domain(client, operator_token):
    resp = await client.post(
        "/api/domains",
        headers=_auth(operator_token),
        json={"name": "operator-domain.com"},
    )
    assert resp.status_code == 403


async def test_domain_list_by_role(client, admin_token, operator_token, client_token):
    resp = await client.get("/api/domains", headers=_auth(admin_token))
    assert resp.status_code == 200
    admin_domains = resp.json()
    assert any(d["name"] == "test.local" for d in admin_domains)

    resp = await client.get("/api/domains", headers=_auth(operator_token))
    assert resp.status_code == 200
    operator_domains = resp.json()
    assert len(operator_domains) == 1
    assert operator_domains[0]["name"] == "test.local"

    resp = await client.get("/api/domains", headers=_auth(client_token))
    assert resp.status_code == 200
    client_domains = resp.json()
    assert len(client_domains) == 1
    assert client_domains[0]["name"] == "test.local"


async def test_duplicate_domain_conflict(client, admin_token):
    resp = await client.post(
        "/api/domains",
        headers=_auth(admin_token),
        json={"name": "duplicate.test"},
    )
    assert resp.status_code == 200

    resp = await client.post(
        "/api/domains",
        headers=_auth(admin_token),
        json={"name": "duplicate.test"},
    )
    assert resp.status_code == 409
    assert "已存在" in resp.json()["message"]


async def test_domain_deactivates_without_removing_row(client, admin_token):
    resp = await client.post(
        "/api/domains",
        headers=_auth(admin_token),
        json={"name": "cycle.test"},
    )
    domain = resp.json()

    resp = await client.get(f"/api/domains/{domain['id']}", headers=_auth(admin_token))
    assert resp.status_code == 200
    assert resp.json()["name"] == "cycle.test"

    resp = await client.put(
        f"/api/domains/{domain['id']}",
        headers=_auth(admin_token),
        json={"name": "cycle-renamed.test"},
    )
    assert resp.status_code == 200
    assert resp.json()["name"] == "cycle-renamed.test"

    resp = await client.delete(
        f"/api/domains/{domain['id']}", headers=_auth(admin_token)
    )
    assert resp.status_code == 200

    resp = await client.get(f"/api/domains/{domain['id']}", headers=_auth(admin_token))
    assert resp.status_code == 200
    assert resp.json()["is_active"] is False
    assert resp.json()["is_default"] is False


async def test_domain_normalizes_host_and_validates_timezone(client, admin_token):
    response = await client.post(
        "/api/domains",
        headers=_auth(admin_token),
        json={"name": "Go.Example.COM", "timezone": "Asia/Shanghai"},
    )
    assert response.status_code == 200
    assert response.json()["name"] == "go.example.com"
    assert response.json()["timezone"] == "Asia/Shanghai"


@pytest.mark.parametrize(
    "name",
    [
        "https://go.example.com",
        "go.example.com/path",
        "go.example.com:443",
        "   ",
    ],
)
async def test_domain_rejects_non_hosts(client, admin_token, name):
    response = await client.post(
        "/api/domains",
        headers=_auth(admin_token),
        json={"name": name},
    )
    assert response.status_code == 400


async def test_domain_rejects_invalid_iana_timezone(client, admin_token):
    response = await client.post(
        "/api/domains",
        headers=_auth(admin_token),
        json={"name": "timezone.example.com", "timezone": "Mars/Olympus"},
    )
    assert response.status_code == 400


# ---------------------------------------------------------------------------
# Admin users
# ---------------------------------------------------------------------------


async def test_admin_user_crud(client, admin_token):
    resp = await client.post(
        "/api/admin/users",
        headers=_auth(admin_token),
        json={
            "username": "newop",
            "password": "password",
            "role": "operator",
            "domain_ids": [],
        },
    )
    assert resp.status_code == 200
    user = resp.json()
    assert user["username"] == "newop"

    resp = await client.get("/api/admin/users", headers=_auth(admin_token))
    assert resp.status_code == 200
    assert any(u["username"] == "newop" for u in resp.json())

    resp = await client.put(
        f"/api/admin/users/{user['id']}",
        headers=_auth(admin_token),
        json={
            "username": "newop2",
            "password": "password2",
            "role": "client",
            "domain_ids": [],
        },
    )
    assert resp.status_code == 200
    assert resp.json()["username"] == "newop2"

    resp = await client.delete(
        f"/api/admin/users/{user['id']}", headers=_auth(admin_token)
    )
    assert resp.status_code == 200

    resp = await client.get("/api/admin/users", headers=_auth(admin_token))
    retained = next(u for u in resp.json() if u["username"] == "newop2")
    assert retained["is_active"] is False


async def test_duplicate_username_conflict(client, admin_token):
    resp = await client.post(
        "/api/admin/users",
        headers=_auth(admin_token),
        json={
            "username": "dupuser",
            "password": "password",
            "role": "client",
            "domain_ids": [],
        },
    )
    assert resp.status_code == 200

    resp = await client.post(
        "/api/admin/users",
        headers=_auth(admin_token),
        json={
            "username": "dupuser",
            "password": "password",
            "role": "client",
            "domain_ids": [],
        },
    )
    assert resp.status_code == 409


@pytest.mark.parametrize("username", [123, None])
async def test_create_user_rejects_non_string_username(client, admin_token, username):
    response = await client.post(
        "/api/admin/users",
        headers=_auth(admin_token),
        json={
            "username": username,
            "password": "secret1",
            "role": "client",
            "domain_ids": [],
        },
    )
    assert response.status_code == 400
    assert response.json() == {
        "code": "VALIDATION_ERROR",
        "message": "usernameInput should be a valid string",
    }


@pytest.mark.parametrize("username", [123, None])
async def test_update_user_rejects_non_string_username(client, admin_token, username):
    users = await client.get("/api/admin/users", headers=_auth(admin_token))
    client_id = next(
        user["id"] for user in users.json() if user["username"] == "client"
    )
    response = await client.put(
        f"/api/admin/users/{client_id}",
        headers=_auth(admin_token),
        json={"username": username},
    )
    assert response.status_code == 400
    assert response.json() == {
        "code": "VALIDATION_ERROR",
        "message": "usernameInput should be a valid string",
    }


async def test_create_user_rejects_username_shortened_below_minimum(
    client, admin_token
):
    response = await client.post(
        "/api/admin/users",
        headers=_auth(admin_token),
        json={
            "username": " ab ",
            "password": "secret1",
            "role": "client",
            "domain_ids": [],
        },
    )
    assert response.status_code == 400
    assert response.json() == {
        "code": "VALIDATION_ERROR",
        "message": "usernameString should have at least 3 characters",
    }


async def test_usernames_are_lowercase_and_case_insensitively_unique(
    client, admin_token
):
    created = await client.post(
        "/api/admin/users",
        headers=_auth(admin_token),
        json={
            "username": "Alice",
            "password": "secret1",
            "role": "client",
            "domain_ids": [],
        },
    )
    assert created.status_code == 200
    assert created.json()["username"] == "alice"
    duplicate = await client.post(
        "/api/admin/users",
        headers=_auth(admin_token),
        json={
            "username": "ALICE",
            "password": "secret1",
            "role": "client",
            "domain_ids": [],
        },
    )
    assert duplicate.status_code == 409


async def test_delete_user_deactivates_without_removing_row(client, admin_token):
    created = await client.post(
        "/api/admin/users",
        headers=_auth(admin_token),
        json={
            "username": "retained",
            "password": "secret1",
            "role": "client",
            "domain_ids": [],
        },
    )
    user_id = created.json()["id"]
    deleted = await client.delete(
        f"/api/admin/users/{user_id}", headers=_auth(admin_token)
    )
    assert deleted.status_code == 200
    users = await client.get("/api/admin/users", headers=_auth(admin_token))
    retained = next(user for user in users.json() if user["id"] == user_id)
    assert retained["is_active"] is False


async def test_user_domain_grants_record_actor_and_preserve_existing_grants(
    client, admin_token, default_domain
):
    target = await client.post(
        "/api/admin/users",
        headers=_auth(admin_token),
        json={
            "username": "grant-target",
            "password": "secret1",
            "role": "client",
            "domain_ids": [default_domain["id"]],
        },
    )
    assert target.status_code == 200
    second_admin = await client.post(
        "/api/admin/users",
        headers=_auth(admin_token),
        json={
            "username": "grant-admin",
            "password": "secret1",
            "role": "admin",
            "domain_ids": [],
        },
    )
    assert second_admin.status_code == 200
    additional_domain = await client.post(
        "/api/domains",
        headers=_auth(admin_token),
        json={"name": "additional-grant.example.com"},
    )
    assert additional_domain.status_code == 200
    login = await client.post(
        "/api/auth/login",
        data={"username": "GRANT-ADMIN", "password": "secret1"},
    )
    assert login.status_code == 200
    second_admin_token = login.json()["access_token"]

    updated = await client.put(
        f"/api/admin/users/{target.json()['id']}",
        headers=_auth(second_admin_token),
        json={"domain_ids": [default_domain["id"], additional_domain.json()["id"]]},
    )
    assert updated.status_code == 200

    with _sync_engine.connect() as connection:
        original_admin_id = connection.scalar(
            text("SELECT id FROM users WHERE username = 'admin'")
        )
        grants = (
            connection.execute(
                text(
                    """
                SELECT domain_id, granted_by
                FROM user_domains
                WHERE user_id = :user_id
                """
                ),
                {"user_id": target.json()["id"]},
            )
            .mappings()
            .all()
        )
    granted_by_domain = {
        str(grant["domain_id"]): str(grant["granted_by"]) for grant in grants
    }
    assert granted_by_domain[default_domain["id"]] == str(original_admin_id)
    assert (
        granted_by_domain[additional_domain.json()["id"]] == second_admin.json()["id"]
    )


# ---------------------------------------------------------------------------
# Short links
# ---------------------------------------------------------------------------


async def test_admin_create_short_link(client, admin_token, default_domain):
    link = await _create_short_link(client, admin_token, default_domain["id"])
    assert link["short_code"]
    assert link["domain_id"] == default_domain["id"]
    assert len(link["short_code"]) == 7
    assert link["name"] == "integration test link"
    assert link["default_action"] == "deny"
    assert "description" not in link


@pytest.mark.parametrize("name", ["", "   ", "x" * 129])
async def test_short_link_create_rejects_blank_or_overlong_name(
    client, admin_token, default_domain, name
):
    response = await client.post(
        "/api/short-links",
        headers={**_auth(admin_token), **_host()},
        json={"domain_id": default_domain["id"], "name": name},
    )
    assert response.status_code == 422


async def test_short_link_create_preserves_nonblank_name_whitespace(
    client, admin_token, default_domain
):
    response = await client.post(
        "/api/short-links",
        headers={**_auth(admin_token), **_host()},
        json={"domain_id": default_domain["id"], "name": "  readable name  "},
    )
    assert response.status_code == 200
    assert response.json()["name"] == "  readable name  "


async def test_short_link_create_mixed_name_and_domain_errors_return_422(
    client, admin_token
):
    response = await client.post(
        "/api/short-links",
        headers={**_auth(admin_token), **_host()},
        json={"domain_id": "not-a-uuid", "name": "   "},
    )
    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_ERROR"

    with pytest.raises(ValidationError) as raised:
        ShortLinkCreate.model_validate({"domain_id": "not-a-uuid", "name": "   "})
    errors = raised.value.errors()
    assert {error["loc"] for error in errors} == {("domain_id",), ("name",)}
    assert (
        next(error for error in errors if error["loc"] == ("name",))["type"]
        == "unprocessable_entity"
    )


async def test_short_link_422_contract_does_not_change_username_validation(
    client, admin_token, default_domain
):
    short_link_response = await client.post(
        "/api/short-links",
        headers={**_auth(admin_token), **_host()},
        json={"domain_id": default_domain["id"], "name": ""},
    )
    assert short_link_response.status_code == 422
    assert short_link_response.json()["code"] == "VALIDATION_ERROR"

    username_response = await client.post(
        "/api/admin/users",
        headers=_auth(admin_token),
        json={
            "username": 123,
            "password": "secret1",
            "role": "client",
            "domain_ids": [],
        },
    )
    assert username_response.status_code == 400
    assert username_response.json()["code"] == "VALIDATION_ERROR"


async def test_operator_create_short_link(client, operator_token, default_domain):
    link = await _create_short_link(client, operator_token, default_domain["id"])
    assert link["short_code"]
    assert link["owner_id"]


async def test_client_cannot_create_short_link(client, client_token, default_domain):
    resp = await client.post(
        "/api/short-links",
        headers={**_auth(client_token), **_host()},
        json={"domain_id": default_domain["id"]},
    )
    assert resp.status_code == 403


async def test_custom_alias(client, admin_token, default_domain):
    resp = await client.post(
        "/api/short-links",
        headers={**_auth(admin_token), **_host()},
        json={
            "domain_id": default_domain["id"],
            "custom_alias": "myalias",
            "name": "custom alias",
        },
    )
    assert resp.status_code == 200
    assert resp.json()["short_code"] == "myalias"
    assert resp.json()["is_custom_alias"] is True


async def test_custom_alias_conflict(client, admin_token, default_domain):
    resp = await client.post(
        "/api/short-links",
        headers={**_auth(admin_token), **_host()},
        json={
            "domain_id": default_domain["id"],
            "custom_alias": "conflict-alias",
            "name": "first custom alias",
        },
    )
    assert resp.status_code == 200

    resp = await client.post(
        "/api/short-links",
        headers={**_auth(admin_token), **_host()},
        json={
            "domain_id": default_domain["id"],
            "custom_alias": "conflict-alias",
            "name": "second custom alias",
        },
    )
    assert resp.status_code == 400
    assert resp.json()["code"] == "INVALID_SHORT_CODE"


async def test_custom_alias_is_canonical_and_redirect_lookup_is_case_insensitive(
    client, admin_token, default_domain
):
    response = await client.post(
        "/api/short-links",
        headers={**_auth(admin_token), **_host()},
        json={
            "domain_id": default_domain["id"],
            "custom_alias": " Promo_7 ",
            "name": "canonical custom alias",
        },
    )
    assert response.status_code == 200, response.text
    link = response.json()
    assert link["short_code"] == "promo_7"
    await _add_target_url(client, admin_token, link["id"], "https://promo.example.test")
    await _add_allow_rule(client, admin_token, link["id"])

    redirect = await client.get(
        "/PROMO_7", headers=_host(), follow_redirects=False
    )
    assert redirect.status_code == 302
    assert redirect.headers["location"] == "https://promo.example.test"

    conflict = await client.post(
        "/api/short-links",
        headers={**_auth(admin_token), **_host()},
        json={
            "domain_id": default_domain["id"],
            "custom_alias": "pRoMo_7",
            "name": "canonical alias conflict",
        },
    )
    assert conflict.status_code == 400
    assert conflict.json()["code"] == "INVALID_SHORT_CODE"


async def test_short_link_list_by_role(
    client, admin_token, operator_token, client_token, default_domain
):
    admin_link = await _create_short_link(client, admin_token, default_domain["id"])
    operator_link = await _create_short_link(
        client, operator_token, default_domain["id"]
    )

    resp = await client.get(
        "/api/short-links", headers={**_auth(admin_token), **_host()}
    )
    assert resp.status_code == 200
    admin_ids = {l["id"] for l in resp.json()}
    assert admin_link["id"] in admin_ids
    assert operator_link["id"] in admin_ids

    resp = await client.get(
        "/api/short-links", headers={**_auth(operator_token), **_host()}
    )
    assert resp.status_code == 200
    operator_ids = {l["id"] for l in resp.json()}
    assert operator_link["id"] in operator_ids
    assert admin_link["id"] not in operator_ids

    resp = await client.get(
        "/api/short-links", headers={**_auth(client_token), **_host()}
    )
    assert resp.status_code == 200
    assert resp.json() == []


async def test_get_update_delete_short_link(client, admin_token, default_domain):
    link = await _create_short_link(client, admin_token, default_domain["id"])

    resp = await client.get(
        f"/api/short-links/{link['id']}",
        headers={**_auth(admin_token), **_host()},
    )
    assert resp.status_code == 200
    assert resp.json()["id"] == link["id"]

    resp = await client.put(
        f"/api/short-links/{link['id']}",
        headers={**_auth(admin_token), **_host()},
        json={"name": "updated"},
    )
    assert resp.status_code == 200
    assert resp.json()["name"] == "updated"

    resp = await client.delete(
        f"/api/short-links/{link['id']}",
        headers={**_auth(admin_token), **_host()},
    )
    assert resp.status_code == 200

    with _sync_engine.connect() as connection:
        deleted = (
            connection.execute(
                text(
                    """
                SELECT is_active, deleted_at IS NOT NULL AS has_deleted_at, deleted_by
                FROM short_links WHERE id = :id
                """
                ),
                {"id": link["id"]},
            )
            .mappings()
            .one()
        )
    assert deleted["is_active"] is False
    assert deleted["has_deleted_at"] is True

    resp = await client.get(
        f"/api/short-links/{link['id']}",
        headers={**_auth(admin_token), **_host()},
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Target URLs
# ---------------------------------------------------------------------------


async def test_target_url_crud(client, admin_token, default_domain):
    link = await _create_short_link(client, admin_token, default_domain["id"])
    url = await _add_target_url(
        client, admin_token, link["id"], "https://target.example.com"
    )
    assert url["url"] == "https://target.example.com"
    assert url["name"] is None
    assert url["updated_at"]

    resp = await client.put(
        f"/api/short-links/{link['id']}/urls/{url['id']}",
        headers={**_auth(admin_token), **_host()},
        json={"url": "https://updated.example.com"},
    )
    assert resp.status_code == 200
    assert resp.json()["url"] == "https://updated.example.com"

    resp = await client.delete(
        f"/api/short-links/{link['id']}/urls/{url['id']}",
        headers={**_auth(admin_token), **_host()},
    )
    assert resp.status_code == 200


async def test_target_url_rejects_zero_weight_and_preserves_optional_name(
    client, admin_token, default_domain
):
    link = await _create_short_link(client, admin_token, default_domain["id"])
    invalid = await client.post(
        f"/api/short-links/{link['id']}/urls",
        headers={**_auth(admin_token), **_host()},
        json={"url": "https://zero.example.com", "url_type": "allowed", "weight": 0},
    )
    assert invalid.status_code == 422

    created = await client.post(
        f"/api/short-links/{link['id']}/urls",
        headers={**_auth(admin_token), **_host()},
        json={
            "url": "https://named.example.com",
            "url_type": "allowed",
            "weight": 1,
            "name": "named target",
        },
    )
    assert created.status_code == 200
    assert created.json()["name"] == "named target"
    assert created.json()["updated_at"]


# ---------------------------------------------------------------------------
# Access rules
# ---------------------------------------------------------------------------


async def test_access_rule_crud(client, admin_token, default_domain):
    link = await _create_short_link(client, admin_token, default_domain["id"])
    resp = await client.post(
        f"/api/short-links/{link['id']}/rules",
        headers={**_auth(admin_token), **_host()},
        json={
            "name": "deny China",
            "action": "deny",
            "priority": 1,
            "countries": ["cn"],
            "ua_platforms": ["MOBILE"],
            "referer_patterns": [" https://example.com/*, *social* ", ""],
            "client_requirement": "human",
            "proxy_requirement": "non_proxy",
        },
    )
    assert resp.status_code == 200
    rule = resp.json()
    assert rule["action"] == "deny"
    assert rule["name"] == "deny China"
    assert rule["countries"] == ["CN"]
    assert rule["ua_platforms"] == ["mobile"]
    assert rule["referer_patterns"] == ["https://example.com/*", "*social*"]
    assert rule["client_requirement"] == "human"
    assert rule["proxy_requirement"] == "non_proxy"

    resp = await client.put(
        f"/api/short-links/{link['id']}/rules/{rule['id']}",
        headers={**_auth(admin_token), **_host()},
        json={"priority": 2},
    )
    assert resp.status_code == 200
    assert resp.json()["priority"] == 2

    resp = await client.delete(
        f"/api/short-links/{link['id']}/rules/{rule['id']}",
        headers={**_auth(admin_token), **_host()},
    )
    assert resp.status_code == 200


async def test_access_rule_requires_name_and_assigns_next_priority(
    client, admin_token, default_domain
):
    link = await _create_short_link(client, admin_token, default_domain["id"])
    missing_name = await client.post(
        f"/api/short-links/{link['id']}/rules",
        headers={**_auth(admin_token), **_host()},
        json={"action": "allow"},
    )
    assert missing_name.status_code == 400

    first = await client.post(
        f"/api/short-links/{link['id']}/rules",
        headers={**_auth(admin_token), **_host()},
        json={"name": "first", "action": "allow"},
    )
    second = await client.post(
        f"/api/short-links/{link['id']}/rules",
        headers={**_auth(admin_token), **_host()},
        json={"name": "second", "action": "deny"},
    )
    assert first.status_code == second.status_code == 200
    assert first.json()["priority"] == 0
    assert second.json()["priority"] == 1


async def test_access_rule_duplicate_priority_rolls_back_as_conflict(
    client, admin_token, default_domain
):
    link = await _create_short_link(client, admin_token, default_domain["id"])
    first = await client.post(
        f"/api/short-links/{link['id']}/rules",
        headers={**_auth(admin_token), **_host()},
        json={"name": "first", "action": "allow", "priority": 3},
    )
    duplicate = await client.post(
        f"/api/short-links/{link['id']}/rules",
        headers={**_auth(admin_token), **_host()},
        json={"name": "duplicate", "action": "deny", "priority": 3},
    )
    assert first.status_code == 200
    assert duplicate.status_code == 409
    assert duplicate.json() == {"code": "CONFLICT", "message": "该优先级已存在"}

    follow_up = await client.post(
        f"/api/short-links/{link['id']}/rules",
        headers={**_auth(admin_token), **_host()},
        json={"name": "next", "action": "allow"},
    )
    assert follow_up.status_code == 200
    assert follow_up.json()["priority"] == 4


async def test_access_rule_update_normalizes_every_rule_field(
    client, admin_token, default_domain
):
    link = await _create_short_link(client, admin_token, default_domain["id"])
    created = await client.post(
        f"/api/short-links/{link['id']}/rules",
        headers={**_auth(admin_token), **_host()},
        json={"name": "initial", "action": "allow"},
    )
    assert created.status_code == 200

    updated = await client.put(
        f"/api/short-links/{link['id']}/rules/{created.json()['id']}",
        headers={**_auth(admin_token), **_host()},
        json={
            "name": "  updated rule  ",
            "countries": ["us", "aq"],
            "ua_platforms": [" Mobile ", "BOT"],
            "referer_patterns": [" https://one.example/*, , *social* "],
        },
    )

    assert updated.status_code == 200
    assert updated.json()["name"] == "updated rule"
    assert updated.json()["countries"] == ["US", "AQ"]
    assert updated.json()["ua_platforms"] == ["mobile", "bot"]
    assert updated.json()["referer_patterns"] == ["https://one.example/*", "*social*"]


@pytest.mark.parametrize(
    ("field", "value", "status_code"),
    [
        ("name", None, 422),
        ("countries", None, 422),
        ("ua_platforms", None, 422),
        ("referer_patterns", None, 422),
        ("name", "   ", 422),
        ("countries", ["ZZ"], 422),
    ],
)
async def test_access_rule_update_rejects_null_or_invalid_normalized_values(
    client, admin_token, default_domain, field, value, status_code
):
    link = await _create_short_link(client, admin_token, default_domain["id"])
    created = await client.post(
        f"/api/short-links/{link['id']}/rules",
        headers={**_auth(admin_token), **_host()},
        json={"name": "initial", "action": "allow"},
    )
    assert created.status_code == 200

    response = await client.put(
        f"/api/short-links/{link['id']}/rules/{created.json()['id']}",
        headers={**_auth(admin_token), **_host()},
        json={field: value},
    )

    assert response.status_code == status_code
    assert response.json()["code"] == "VALIDATION_ERROR"


async def test_access_rule_create_rejects_non_iso_country_code(
    client, admin_token, default_domain
):
    link = await _create_short_link(client, admin_token, default_domain["id"])

    response = await client.post(
        f"/api/short-links/{link['id']}/rules",
        headers={**_auth(admin_token), **_host()},
        json={"name": "invalid country", "action": "allow", "countries": ["ZZ"]},
    )

    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_ERROR"


# ---------------------------------------------------------------------------
# Redirect & logs
# ---------------------------------------------------------------------------


async def test_redirect_to_target(client, admin_token, default_domain):
    link = await _create_short_link(client, admin_token, default_domain["id"])
    await _add_target_url(
        client, admin_token, link["id"], "https://redirect.example.com"
    )
    await _add_allow_rule(client, admin_token, link["id"])

    resp = await client.get(
        f"/{link['short_code']}",
        headers=_host(),
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert resp.headers["location"] == "https://redirect.example.com"


async def test_head_redirect_preserves_plain_302_contract(
    client, admin_token, default_domain
):
    link = await _create_short_link(client, admin_token, default_domain["id"])
    await _add_target_url(client, admin_token, link["id"], "https://head.example.com")
    await _add_allow_rule(client, admin_token, link["id"])

    resp = await client.head(
        f"/{link['short_code']}",
        headers=_host(),
        follow_redirects=False,
    )

    assert resp.status_code == 302
    assert resp.headers["location"] == "https://head.example.com"
    assert resp.content == b""


async def test_redirect_unknown_short_code(client):
    resp = await client.get("/notexist", headers=_host(), follow_redirects=False)
    assert resp.status_code == 404


async def test_redirect_denied_by_rule(client, admin_token, default_domain):
    link = await _create_short_link(client, admin_token, default_domain["id"])
    await _add_target_url(client, admin_token, link["id"], "https://deny.example.com")
    await client.post(
        f"/api/short-links/{link['id']}/rules",
        headers={**_auth(admin_token), **_host()},
        json={
            "name": "deny mobile",
            "action": "deny",
            "priority": 1,
            "ua_platforms": ["mobile"],
        },
    )

    resp = await client.get(
        f"/{link['short_code']}",
        headers={
            **_host(),
            "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X)",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 403


async def test_logs_and_daily_stats(client, admin_token, default_domain):
    link = await _create_short_link(client, admin_token, default_domain["id"])
    await _add_target_url(client, admin_token, link["id"], "https://stats.example.com")
    await _add_allow_rule(client, admin_token, link["id"])

    for _ in range(3):
        await client.get(
            f"/{link['short_code']}",
            headers={**_host(), "X-Forwarded-For": "1.2.3.4"},
            follow_redirects=False,
        )

    resp = await client.get(
        "/api/logs",
        headers={**_auth(admin_token), **_host()},
        params={"short_link_id": link["id"]},
    )
    assert resp.status_code == 200
    logs = resp.json()
    assert len(logs) == 3
    assert logs[0]["result"] == "allowed"

    resp = await client.get(
        "/api/logs/daily",
        headers={**_auth(admin_token), **_host()},
        params={"short_link_id": link["id"]},
    )
    assert resp.status_code == 200
    stats = resp.json()
    assert len(stats) == 1
    assert stats[0]["total"] == 3
    assert stats[0]["unique_ips"] == 1


# ---------------------------------------------------------------------------
# Permissions
# ---------------------------------------------------------------------------


async def test_client_can_view_granted_link(
    client, admin_token, client_token, default_domain
):
    link = await _create_short_link(client, admin_token, default_domain["id"])

    resp = await client.get(
        f"/api/short-links/{link['id']}",
        headers={**_auth(client_token), **_host()},
    )
    assert resp.status_code == 403

    me = await client.get("/api/auth/me", headers=_auth(client_token))
    client_id = me.json()["id"]

    resp = await client.post(
        f"/api/short-links/{link['id']}/permissions",
        headers={**_auth(admin_token), **_host()},
        json={"user_id": client_id},
    )
    assert resp.status_code == 200
    assert (
        resp.json()["granted_by"]
        == (await client.get("/api/auth/me", headers=_auth(admin_token))).json()["id"]
    )

    resp = await client.get(
        f"/api/short-links/{link['id']}",
        headers={**_auth(client_token), **_host()},
    )
    assert resp.status_code == 200
    assert resp.json()["id"] == link["id"]

    resp = await client.delete(
        f"/api/short-links/{link['id']}/permissions/{client_id}",
        headers={**_auth(admin_token), **_host()},
    )
    assert resp.status_code == 200

    resp = await client.get(
        f"/api/short-links/{link['id']}",
        headers={**_auth(client_token), **_host()},
    )
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Short link error branches
# ---------------------------------------------------------------------------


async def test_create_short_link_domain_not_found(client, admin_token):
    resp = await client.post(
        "/api/short-links",
        headers={**_auth(admin_token), **_host()},
        json={
            "domain_id": "00000000-0000-0000-0000-000000000000",
            "name": "missing domain",
        },
    )
    assert resp.status_code == 404


async def test_create_short_link_inactive_domain(client, admin_token):
    resp = await client.post(
        "/api/domains",
        headers=_auth(admin_token),
        json={"name": "inactive.test", "is_active": False},
    )
    assert resp.status_code == 200
    domain = resp.json()

    resp = await client.post(
        "/api/short-links",
        headers={**_auth(admin_token), **_host("inactive.test")},
        json={"domain_id": domain["id"], "name": "inactive domain"},
    )
    assert resp.status_code == 400
    assert resp.json()["code"] == "DOMAIN_INACTIVE"


async def test_operator_cannot_create_in_unauthorized_domain(
    client, admin_token, operator_token, default_domain
):
    resp = await client.post(
        "/api/domains",
        headers=_auth(admin_token),
        json={"name": "private.test"},
    )
    assert resp.status_code == 200
    private_domain = resp.json()

    resp = await client.post(
        "/api/short-links",
        headers={**_auth(operator_token), **_host("private.test")},
        json={"domain_id": private_domain["id"], "name": "private domain"},
    )
    assert resp.status_code == 403


async def test_target_url_not_found(client, admin_token, default_domain):
    link = await _create_short_link(client, admin_token, default_domain["id"])

    resp = await client.put(
        f"/api/short-links/{link['id']}/urls/00000000-0000-0000-0000-000000000000",
        headers={**_auth(admin_token), **_host()},
        json={"url": "https://nope.example.com"},
    )
    assert resp.status_code == 404

    resp = await client.delete(
        f"/api/short-links/{link['id']}/urls/00000000-0000-0000-0000-000000000000",
        headers={**_auth(admin_token), **_host()},
    )
    assert resp.status_code == 404


async def test_access_rule_not_found(client, admin_token, default_domain):
    link = await _create_short_link(client, admin_token, default_domain["id"])

    resp = await client.put(
        f"/api/short-links/{link['id']}/rules/00000000-0000-0000-0000-000000000000",
        headers={**_auth(admin_token), **_host()},
        json={"priority": 5},
    )
    assert resp.status_code == 404

    resp = await client.delete(
        f"/api/short-links/{link['id']}/rules/00000000-0000-0000-0000-000000000000",
        headers={**_auth(admin_token), **_host()},
    )
    assert resp.status_code == 404


async def test_permission_grant_requires_client_user(
    client, admin_token, default_domain
):
    link = await _create_short_link(client, admin_token, default_domain["id"])

    resp = await client.post(
        f"/api/short-links/{link['id']}/permissions",
        headers={**_auth(admin_token), **_host()},
        json={"user_id": "00000000-0000-0000-0000-000000000000"},
    )
    assert resp.status_code == 404


async def test_permission_grant_requires_domain_access(
    client, admin_token, operator_token, default_domain
):
    # create a new client user without domain access
    resp = await client.post(
        "/api/admin/users",
        headers=_auth(admin_token),
        json={
            "username": "nodomain",
            "password": "password",
            "role": "client",
            "domain_ids": [],
        },
    )
    assert resp.status_code == 200
    isolated_client = resp.json()

    link = await _create_short_link(client, operator_token, default_domain["id"])

    resp = await client.post(
        f"/api/short-links/{link['id']}/permissions",
        headers={**_auth(operator_token), **_host()},
        json={"user_id": isolated_client["id"]},
    )
    assert resp.status_code == 403


async def test_revoke_permission_not_found(client, admin_token, default_domain):
    link = await _create_short_link(client, admin_token, default_domain["id"])

    resp = await client.delete(
        f"/api/short-links/{link['id']}/permissions/00000000-0000-0000-0000-000000000000",
        headers={**_auth(admin_token), **_host()},
    )
    assert resp.status_code == 404


async def test_redirect_inactive_short_link(client, admin_token, default_domain):
    link = await _create_short_link(client, admin_token, default_domain["id"])
    await _add_target_url(
        client, admin_token, link["id"], "https://inactive.example.com"
    )

    resp = await client.put(
        f"/api/short-links/{link['id']}",
        headers={**_auth(admin_token), **_host()},
        json={"is_active": False},
    )
    assert resp.status_code == 200

    resp = await client.get(
        f"/{link['short_code']}",
        headers=_host(),
        follow_redirects=False,
    )
    assert resp.status_code == 404


async def test_redirect_no_target_url(client, admin_token, default_domain):
    link = await _create_short_link(client, admin_token, default_domain["id"])

    resp = await client.get(
        f"/{link['short_code']}",
        headers=_host(),
        follow_redirects=False,
    )
    assert resp.status_code == 403
    assert resp.json()["code"] == "PERMISSION_DENIED"


async def test_login_disabled_user(client, admin_token):
    resp = await client.post(
        "/api/admin/users",
        headers=_auth(admin_token),
        json={
            "username": "disabled",
            "password": "password",
            "role": "operator",
            "domain_ids": [],
        },
    )
    assert resp.status_code == 200
    user = resp.json()

    with _sync_engine.connect() as conn:
        conn.execute(
            text("UPDATE users SET is_active = false WHERE id = :id"),
            {"id": user["id"]},
        )
        conn.commit()

    resp = await client.post(
        "/api/auth/login",
        data={"username": "disabled", "password": "password"},
    )
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Blacklist redirect
# ---------------------------------------------------------------------------


async def test_redirect_blacklisted_ip(
    client, admin_token, default_domain, monkeypatch
):
    monkeypatch.setattr(settings, "trust_proxy_headers", True)
    link = await _create_short_link(client, admin_token, default_domain["id"])
    await _add_allow_rule(client, admin_token, link["id"])
    await _add_target_url(
        client, admin_token, link["id"], "https://blocked.example.com"
    )

    await client.post(
        "/api/ip-blacklist",
        headers=_auth(admin_token),
        json={"ip": "192.168.1.1", "reason": "test"},
    )

    resp = await client.get(
        f"/{link['short_code']}",
        headers={**_host(), "X-Real-IP": "192.168.1.1"},
        follow_redirects=False,
    )
    assert resp.status_code == 403
    assert resp.json()["code"] == "PERMISSION_DENIED"


async def test_redirect_ignores_expired_or_removed_blacklist_entries(
    client, admin_token, default_domain, monkeypatch
):
    monkeypatch.setattr(settings, "trust_proxy_headers", True)
    link = await _create_short_link(client, admin_token, default_domain["id"])
    await _add_allow_rule(client, admin_token, link["id"])
    await _add_target_url(
        client, admin_token, link["id"], "https://allowed.example.com"
    )
    expired = await client.post(
        "/api/ip-blacklist",
        headers=_auth(admin_token),
        json={
            "ip": "192.168.1.2",
            "reason": "short lived",
            "expires_at": "2100-01-01T00:00:00+00:00",
        },
    )
    assert expired.status_code == 200
    with _sync_engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE ip_blacklist SET expires_at = now() - interval '1 second' "
                "WHERE id = :id"
            ),
            {"id": expired.json()["id"]},
        )

    expired_response = await client.get(
        f"/{link['short_code']}",
        headers={**_host(), "X-Real-IP": "192.168.1.2"},
        follow_redirects=False,
    )
    assert expired_response.status_code == 302

    removed = await client.post(
        "/api/ip-blacklist",
        headers=_auth(admin_token),
        json={"ip": "192.168.1.3", "reason": "removed entry"},
    )
    assert removed.status_code == 200
    assert (
        await client.delete(
            f"/api/ip-blacklist/{removed.json()['id']}", headers=_auth(admin_token)
        )
    ).status_code == 200
    removed_response = await client.get(
        f"/{link['short_code']}",
        headers={**_host(), "X-Real-IP": "192.168.1.3"},
        follow_redirects=False,
    )
    assert removed_response.status_code == 302


@pytest.mark.parametrize(
    ("trust_proxy_headers", "real_ip"),
    [(False, "198.51.100.7"), (True, "not-an-ip")],
)
async def test_public_redirect_allows_unknown_client_ip_without_database_cast(
    client,
    admin_token,
    default_domain,
    monkeypatch,
    trust_proxy_headers,
    real_ip,
):
    monkeypatch.setattr(settings, "trust_proxy_headers", trust_proxy_headers)
    link = await _create_short_link(client, admin_token, default_domain["id"])
    await _add_allow_rule(client, admin_token, link["id"])
    await _add_target_url(
        client, admin_token, link["id"], "https://unknown-ip.example.com"
    )

    transport = ASGITransport(app=app, client=None)
    async with AsyncClient(
        transport=transport, base_url="http://test"
    ) as unknown_client:
        response = await unknown_client.get(
            f"/{link['short_code']}",
            headers={
                **_host(),
                "X-Real-IP": real_ip,
                "X-Forwarded-For": "not-an-ip",
            },
            follow_redirects=False,
        )
    assert response.status_code == 302
    assert response.headers["location"] == "https://unknown-ip.example.com"
    logs = await client.get(
        "/api/logs",
        headers={**_auth(admin_token), **_host()},
        params={"short_link_id": link["id"]},
    )
    assert logs.status_code == 200
    assert logs.json()[0]["client_ip"] is None
    assert logs.json()[0]["request_host"] == "test.local"
