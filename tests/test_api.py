"""End-to-end API integration tests."""

from sqlalchemy import text
from httpx import AsyncClient

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
        json={"domain_id": domain_id, "description": "integration test link"},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _add_target_url(client: AsyncClient, token: str, link_id: str, url: str = "https://example.com") -> dict:
    resp = await client.post(
        f"/api/short-links/{link_id}/urls",
        headers={**_auth(token), **_host()},
        json={"url": url, "url_type": "allowed", "weight": 1, "is_active": True},
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


async def test_get_update_delete_domain(client, admin_token):
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

    resp = await client.delete(f"/api/domains/{domain['id']}", headers=_auth(admin_token))
    assert resp.status_code == 200

    resp = await client.get(f"/api/domains/{domain['id']}", headers=_auth(admin_token))
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Admin users
# ---------------------------------------------------------------------------


async def test_admin_user_crud(client, admin_token):
    resp = await client.post(
        "/api/admin/users",
        headers=_auth(admin_token),
        json={"username": "newop", "password": "password", "role": "operator", "domain_ids": []},
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
        json={"username": "newop2", "password": "password2", "role": "client", "domain_ids": []},
    )
    assert resp.status_code == 200
    assert resp.json()["username"] == "newop2"

    resp = await client.delete(f"/api/admin/users/{user['id']}", headers=_auth(admin_token))
    assert resp.status_code == 200

    resp = await client.get("/api/admin/users", headers=_auth(admin_token))
    assert not any(u["username"] == "newop2" for u in resp.json())


async def test_duplicate_username_conflict(client, admin_token):
    resp = await client.post(
        "/api/admin/users",
        headers=_auth(admin_token),
        json={"username": "dupuser", "password": "password", "role": "client", "domain_ids": []},
    )
    assert resp.status_code == 200

    resp = await client.post(
        "/api/admin/users",
        headers=_auth(admin_token),
        json={"username": "dupuser", "password": "password", "role": "client", "domain_ids": []},
    )
    assert resp.status_code == 409


# ---------------------------------------------------------------------------
# Short links
# ---------------------------------------------------------------------------


async def test_admin_create_short_link(client, admin_token, default_domain):
    link = await _create_short_link(client, admin_token, default_domain["id"])
    assert link["short_code"]
    assert link["domain_id"] == default_domain["id"]
    assert len(link["short_code"]) == 6


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
        json={"domain_id": default_domain["id"], "custom_alias": "myalias"},
    )
    assert resp.status_code == 200
    assert resp.json()["short_code"] == "myalias"
    assert resp.json()["is_custom_alias"] is True


async def test_custom_alias_conflict(client, admin_token, default_domain):
    resp = await client.post(
        "/api/short-links",
        headers={**_auth(admin_token), **_host()},
        json={"domain_id": default_domain["id"], "custom_alias": "conflict-alias"},
    )
    assert resp.status_code == 200

    resp = await client.post(
        "/api/short-links",
        headers={**_auth(admin_token), **_host()},
        json={"domain_id": default_domain["id"], "custom_alias": "conflict-alias"},
    )
    assert resp.status_code == 400
    assert resp.json()["code"] == "INVALID_SHORT_CODE"


async def test_short_link_list_by_role(client, admin_token, operator_token, client_token, default_domain):
    admin_link = await _create_short_link(client, admin_token, default_domain["id"])
    operator_link = await _create_short_link(client, operator_token, default_domain["id"])

    resp = await client.get("/api/short-links", headers={**_auth(admin_token), **_host()})
    assert resp.status_code == 200
    admin_ids = {l["id"] for l in resp.json()}
    assert admin_link["id"] in admin_ids
    assert operator_link["id"] in admin_ids

    resp = await client.get("/api/short-links", headers={**_auth(operator_token), **_host()})
    assert resp.status_code == 200
    operator_ids = {l["id"] for l in resp.json()}
    assert operator_link["id"] in operator_ids
    assert admin_link["id"] not in operator_ids

    resp = await client.get("/api/short-links", headers={**_auth(client_token), **_host()})
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
        json={"description": "updated"},
    )
    assert resp.status_code == 200
    assert resp.json()["description"] == "updated"

    resp = await client.delete(
        f"/api/short-links/{link['id']}",
        headers={**_auth(admin_token), **_host()},
    )
    assert resp.status_code == 200

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
    url = await _add_target_url(client, admin_token, link["id"], "https://target.example.com")
    assert url["url"] == "https://target.example.com"

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


# ---------------------------------------------------------------------------
# Access rules
# ---------------------------------------------------------------------------


async def test_access_rule_crud(client, admin_token, default_domain):
    link = await _create_short_link(client, admin_token, default_domain["id"])
    resp = await client.post(
        f"/api/short-links/{link['id']}/rules",
        headers={**_auth(admin_token), **_host()},
        json={"action": "deny", "priority": 1, "countries": ["CN"]},
    )
    assert resp.status_code == 200
    rule = resp.json()
    assert rule["action"] == "deny"

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


# ---------------------------------------------------------------------------
# Redirect & logs
# ---------------------------------------------------------------------------


async def test_redirect_to_target(client, admin_token, default_domain):
    link = await _create_short_link(client, admin_token, default_domain["id"])
    await _add_target_url(client, admin_token, link["id"], "https://redirect.example.com")

    resp = await client.get(
        f"/{link['short_code']}",
        headers=_host(),
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert resp.headers["location"] == "https://redirect.example.com"


async def test_redirect_unknown_short_code(client):
    resp = await client.get("/notexist", headers=_host(), follow_redirects=False)
    assert resp.status_code == 404


async def test_redirect_denied_by_rule(client, admin_token, default_domain):
    link = await _create_short_link(client, admin_token, default_domain["id"])
    await _add_target_url(client, admin_token, link["id"], "https://deny.example.com")
    await client.post(
        f"/api/short-links/{link['id']}/rules",
        headers={**_auth(admin_token), **_host()},
        json={"action": "deny", "priority": 1, "ua_platforms": ["mobile"]},
    )

    resp = await client.get(
        f"/{link['short_code']}",
        headers={**_host(), "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X)"},
        follow_redirects=False,
    )
    assert resp.status_code == 403


async def test_logs_and_daily_stats(client, admin_token, default_domain):
    link = await _create_short_link(client, admin_token, default_domain["id"])
    await _add_target_url(client, admin_token, link["id"], "https://stats.example.com")

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


async def test_client_can_view_granted_link(client, admin_token, client_token, default_domain):
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
        json={"domain_id": "00000000-0000-0000-0000-000000000000"},
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
        json={"domain_id": domain["id"]},
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
        json={"domain_id": private_domain["id"]},
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


async def test_permission_grant_requires_client_user(client, admin_token, default_domain):
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
        json={"username": "nodomain", "password": "password", "role": "client", "domain_ids": []},
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
    await _add_target_url(client, admin_token, link["id"], "https://inactive.example.com")

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
    assert resp.status_code == 404
    assert resp.json()["code"] == "NOT_FOUND"


async def test_login_disabled_user(client, admin_token):
    resp = await client.post(
        "/api/admin/users",
        headers=_auth(admin_token),
        json={"username": "disabled", "password": "password", "role": "operator", "domain_ids": []},
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


async def test_redirect_blacklisted_ip(client, admin_token, default_domain):
    link = await _create_short_link(client, admin_token, default_domain["id"])
    await _add_target_url(client, admin_token, link["id"], "https://blocked.example.com")

    await client.post(
        "/api/ip-blacklist",
        headers=_auth(admin_token),
        json={"ip": "192.168.1.1", "reason": "test"},
    )

    resp = await client.get(
        f"/{link['short_code']}",
        headers={**_host(), "X-Forwarded-For": "192.168.1.1"},
        follow_redirects=False,
    )
    assert resp.status_code == 403
    assert resp.json()["code"] == "PERMISSION_DENIED"

