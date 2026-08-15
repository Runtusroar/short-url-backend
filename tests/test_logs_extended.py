"""Extended access log query tests."""

from datetime import date, timedelta

from httpx import AsyncClient


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _host(domain: str = "test.local") -> dict:
    return {"Host": domain}


async def _create_link_and_log(client: AsyncClient, token: str, domain_id: str) -> dict:
    resp = await client.post(
        "/api/short-links",
        headers={**_auth(token), **_host()},
        json={"domain_id": domain_id, "description": "log test"},
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
