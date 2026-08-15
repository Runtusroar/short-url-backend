"""IP blacklist endpoint tests."""

from httpx import AsyncClient


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def test_admin_blacklist_crud(client: AsyncClient, admin_token: str):
    resp = await client.get("/api/ip-blacklist", headers=_auth(admin_token))
    assert resp.status_code == 200
    initial = resp.json()

    resp = await client.post(
        "/api/ip-blacklist",
        headers=_auth(admin_token),
        json={"ip": "10.0.0.1", "reason": "test"},
    )
    assert resp.status_code == 200
    entry = resp.json()
    assert entry["ip"] == "10.0.0.1"
    assert entry["reason"] == "test"

    resp = await client.get("/api/ip-blacklist", headers=_auth(admin_token))
    assert resp.status_code == 200
    assert len(resp.json()) == len(initial) + 1

    resp = await client.delete(
        f"/api/ip-blacklist/{entry['id']}",
        headers=_auth(admin_token),
    )
    assert resp.status_code == 200

    resp = await client.get("/api/ip-blacklist", headers=_auth(admin_token))
    assert len(resp.json()) == len(initial)


async def test_blacklist_duplicate(client: AsyncClient, admin_token: str):
    resp = await client.post(
        "/api/ip-blacklist",
        headers=_auth(admin_token),
        json={"ip": "10.0.0.2"},
    )
    assert resp.status_code == 200

    resp = await client.post(
        "/api/ip-blacklist",
        headers=_auth(admin_token),
        json={"ip": "10.0.0.2"},
    )
    assert resp.status_code == 409


async def test_operator_can_manage_blacklist(client: AsyncClient, operator_token: str):
    resp = await client.get("/api/ip-blacklist", headers=_auth(operator_token))
    assert resp.status_code == 200


async def test_client_cannot_access_blacklist(client: AsyncClient, client_token: str):
    resp = await client.get("/api/ip-blacklist", headers=_auth(client_token))
    assert resp.status_code == 403

    resp = await client.post(
        "/api/ip-blacklist",
        headers=_auth(client_token),
        json={"ip": "10.0.0.3"},
    )
    assert resp.status_code == 403
