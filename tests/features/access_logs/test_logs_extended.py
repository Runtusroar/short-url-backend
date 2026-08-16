"""Extended access log query tests."""

from datetime import date, timedelta

from httpx import AsyncClient

from app.core.config import settings


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _host(domain: str = "test.local") -> dict:
    return {"Host": domain}


async def _create_link_and_log(client: AsyncClient, token: str, domain_id: str) -> dict:
    resp = await client.post(
        "/api/short-links",
        headers={**_auth(token), **_host()},
        json={"domain_id": domain_id, "name": "log test"},
    )
    assert resp.status_code == 200
    link = resp.json()

    resp = await client.post(
        f"/api/short-links/{link['id']}/urls",
        headers={**_auth(token), **_host()},
        json={"url": "https://log.example.com", "url_type": "allowed", "weight": 1, "is_active": True},
    )
    assert resp.status_code == 200

    for _ in range(2):
        await client.get(
            f"/{link['short_code']}",
            headers={**_host(), "X-Forwarded-For": "10.0.0.10"},
            follow_redirects=False,
        )

    return link


async def test_operator_can_view_own_link_logs(
    client: AsyncClient, operator_token: str, default_domain: dict
):
    link = await _create_link_and_log(client, operator_token, default_domain["id"])

    resp = await client.get(
        "/api/logs",
        headers={**_auth(operator_token), **_host()},
        params={"short_link_id": link["id"]},
    )
    assert resp.status_code == 200
    assert len(resp.json()) == 2


async def test_soft_deleted_short_link_keeps_authorized_historical_logs(
    client: AsyncClient, operator_token: str, default_domain: dict
):
    link = await _create_link_and_log(client, operator_token, default_domain["id"])

    deleted = await client.delete(
        f"/api/short-links/{link['id']}",
        headers={**_auth(operator_token), **_host()},
    )
    assert deleted.status_code == 200

    redirect = await client.get(
        f"/{link['short_code']}", headers=_host(), follow_redirects=False
    )
    assert redirect.status_code == 404

    logs = await client.get(
        "/api/logs",
        headers={**_auth(operator_token), **_host()},
        params={"short_link_id": link["id"]},
    )
    assert logs.status_code == 200
    assert len(logs.json()) == 2


async def test_operator_cannot_view_other_link_logs(
    client: AsyncClient, admin_token: str, operator_token: str, default_domain: dict
):
    link = await _create_link_and_log(client, admin_token, default_domain["id"])

    resp = await client.get(
        "/api/logs",
        headers={**_auth(operator_token), **_host()},
        params={"short_link_id": link["id"]},
    )
    assert resp.status_code == 403


async def test_client_can_view_granted_link_logs(
    client: AsyncClient, admin_token: str, client_token: str, default_domain: dict
):
    link = await _create_link_and_log(client, admin_token, default_domain["id"])

    me = await client.get("/api/auth/me", headers=_auth(client_token))
    client_id = me.json()["id"]

    await client.post(
        f"/api/short-links/{link['id']}/permissions",
        headers={**_auth(admin_token), **_host()},
        json={"user_id": client_id},
    )

    resp = await client.get(
        "/api/logs",
        headers={**_auth(client_token), **_host()},
        params={"short_link_id": link["id"]},
    )
    assert resp.status_code == 200
    assert len(resp.json()) == 2


async def test_logs_date_filter(
    client: AsyncClient, admin_token: str, default_domain: dict
):
    link = await _create_link_and_log(client, admin_token, default_domain["id"])

    today = date.today()
    yesterday = today - timedelta(days=1)

    resp = await client.get(
        "/api/logs",
        headers={**_auth(admin_token), **_host()},
        params={
            "short_link_id": link["id"],
            "date_from": str(today),
            "date_to": str(today),
        },
    )
    assert resp.status_code == 200
    assert len(resp.json()) == 2

    resp = await client.get(
        "/api/logs",
        headers={**_auth(admin_token), **_host()},
        params={
            "short_link_id": link["id"],
            "date_from": str(yesterday),
            "date_to": str(yesterday),
        },
    )
    assert resp.status_code == 200
    assert len(resp.json()) == 0


async def test_logs_pagination(
    client: AsyncClient, admin_token: str, default_domain: dict
):
    link = await _create_link_and_log(client, admin_token, default_domain["id"])

    resp = await client.get(
        "/api/logs",
        headers={**_auth(admin_token), **_host()},
        params={"short_link_id": link["id"], "limit": 1, "offset": 0},
    )
    assert resp.status_code == 200
    assert len(resp.json()) == 1

    resp = await client.get(
        "/api/logs",
        headers={**_auth(admin_token), **_host()},
        params={"short_link_id": link["id"], "limit": 1, "offset": 10},
    )
    assert resp.status_code == 200
    assert len(resp.json()) == 0


async def test_logs_preserve_rule_and_target_snapshots_after_management_changes(
    client: AsyncClient, admin_token: str, default_domain: dict
):
    link = await client.post(
        "/api/short-links",
        headers={**_auth(admin_token), **_host()},
        json={"domain_id": default_domain["id"], "name": "snapshot test"},
    )
    assert link.status_code == 200
    link = link.json()
    target = await client.post(
        f"/api/short-links/{link['id']}/urls",
        headers={**_auth(admin_token), **_host()},
        json={"url": "https://before.example", "url_type": "allowed", "weight": 1},
    )
    assert target.status_code == 200
    target = target.json()
    rule = await client.post(
        f"/api/short-links/{link['id']}/rules",
        headers={**_auth(admin_token), **_host()},
        json={"name": "allow snapshot", "action": "allow", "priority": 0},
    )
    assert rule.status_code == 200
    rule = rule.json()

    for method in ("get", "head"):
        response = await getattr(client, method)(
            f"/{link['short_code']}",
            headers={**_host("TEST.LOCAL:80"), "User-Agent": "Mozilla/5.0"},
            follow_redirects=False,
        )
        assert response.status_code == 302

    updated = await client.put(
        f"/api/short-links/{link['id']}/urls/{target['id']}",
        headers={**_auth(admin_token), **_host()},
        json={"url": "https://after.example"},
    )
    assert updated.status_code == 200
    assert (
        await client.delete(
            f"/api/short-links/{link['id']}/rules/{rule['id']}",
            headers={**_auth(admin_token), **_host()},
        )
    ).status_code == 200
    assert (
        await client.delete(
            f"/api/short-links/{link['id']}/urls/{target['id']}",
            headers={**_auth(admin_token), **_host()},
        )
    ).status_code == 200

    logs = await client.get(
        "/api/logs",
        headers={**_auth(admin_token), **_host()},
        params={"short_link_id": link["id"]},
    )
    assert logs.status_code == 200
    assert {row["request_method"] for row in logs.json()} == {"GET", "HEAD"}
    for row in logs.json():
        assert row["request_host"] == "test.local"
        assert row["decision_reason"] == "matched_rule"
        assert row["matched_rule_id"] is None
        assert row["matched_rule_name"] == "allow snapshot"
        assert row["target_url_id"] is None
        assert row["target_url_snapshot"] == "https://before.example"


async def test_logs_record_explicit_default_and_blacklist_reasons(
    client: AsyncClient, admin_token: str, default_domain: dict, monkeypatch
):
    link = await client.post(
        "/api/short-links",
        headers={**_auth(admin_token), **_host()},
        json={"domain_id": default_domain["id"], "name": "decision reason test"},
    )
    assert link.status_code == 200
    link = link.json()
    denied_target = await client.post(
        f"/api/short-links/{link['id']}/urls",
        headers={**_auth(admin_token), **_host()},
        json={"url": "https://denied.example", "url_type": "denied", "weight": 1},
    )
    assert denied_target.status_code == 200
    default_denied = await client.get(
        f"/{link['short_code']}", headers=_host(), follow_redirects=False
    )
    assert default_denied.status_code == 302

    monkeypatch.setattr(settings, "trust_proxy_headers", True)
    assert (
        await client.post(
            "/api/ip-blacklist",
            headers=_auth(admin_token),
            json={"ip": "203.0.113.77", "reason": "reason test"},
        )
    ).status_code == 200
    blacklisted = await client.get(
        f"/{link['short_code']}",
        headers={**_host(), "X-Real-IP": "203.0.113.77"},
        follow_redirects=False,
    )
    assert blacklisted.status_code == 302

    logs = await client.get(
        "/api/logs",
        headers={**_auth(admin_token), **_host()},
        params={"short_link_id": link["id"]},
    )
    assert logs.status_code == 200
    assert {row["decision_reason"] for row in logs.json()} == {
        "default_action",
        "blacklist",
    }
