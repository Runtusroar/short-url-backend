"""IP blacklist endpoint tests."""

from datetime import UTC, datetime, timedelta

from httpx import AsyncClient
from sqlalchemy import text

from tests.conftest import _sync_engine


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
        json={"ip": "10.0.0.2", "reason": "duplicate test"},
    )
    assert resp.status_code == 200

    resp = await client.post(
        "/api/ip-blacklist",
        headers=_auth(admin_token),
        json={"ip": "10.0.0.2", "reason": "duplicate test"},
    )
    assert resp.status_code == 409


async def test_blacklist_canonicalizes_ipv6_and_rejects_equivalent_active_address(
    client: AsyncClient, admin_token: str
):
    created = await client.post(
        "/api/ip-blacklist",
        headers=_auth(admin_token),
        json={"ip": "2001:0db8:0:0:0:0:0:1", "reason": "IPv6 test"},
    )

    assert created.status_code == 200
    assert created.json()["ip"] == "2001:db8::1"

    duplicate = await client.post(
        "/api/ip-blacklist",
        headers=_auth(admin_token),
        json={"ip": "2001:db8::1", "reason": "same IPv6"},
    )
    assert duplicate.status_code == 409


async def test_blacklist_requires_nonblank_reason_and_future_expiration(
    client: AsyncClient, admin_token: str
):
    blank_reason = await client.post(
        "/api/ip-blacklist",
        headers=_auth(admin_token),
        json={"ip": "10.0.0.20", "reason": "  "},
    )
    assert blank_reason.status_code == 422

    expired = await client.post(
        "/api/ip-blacklist",
        headers=_auth(admin_token),
        json={
            "ip": "10.0.0.21",
            "reason": "past expiration",
            "expires_at": (datetime.now(UTC) - timedelta(seconds=1)).isoformat(),
        },
    )
    assert expired.status_code == 422


async def test_blacklist_removal_hides_row_and_readding_reuses_it(
    client: AsyncClient, admin_token: str
):
    created = await client.post(
        "/api/ip-blacklist",
        headers=_auth(admin_token),
        json={"ip": "10.0.0.22", "reason": "remove me"},
    )
    assert created.status_code == 200
    entry_id = created.json()["id"]

    removed = await client.delete(
        f"/api/ip-blacklist/{entry_id}", headers=_auth(admin_token)
    )
    assert removed.status_code == 200

    with _sync_engine.connect() as connection:
        lifecycle = (
            connection.execute(
                text(
                    """
                SELECT removed_at, removed_by, removal_reason
                FROM ip_blacklist WHERE id = :id
                """
                ),
                {"id": entry_id},
            )
            .mappings()
            .one()
        )
    assert lifecycle["removed_at"] is not None
    assert lifecycle["removed_by"] is not None
    assert lifecycle["removal_reason"] == "Removed by staff"

    listed = await client.get("/api/ip-blacklist", headers=_auth(admin_token))
    assert entry_id not in {entry["id"] for entry in listed.json()}

    reactivated = await client.post(
        "/api/ip-blacklist",
        headers=_auth(admin_token),
        json={"ip": "10.0.0.22", "reason": "back again"},
    )
    assert reactivated.status_code == 200
    assert reactivated.json()["id"] == entry_id
    assert reactivated.json()["reason"] == "back again"

    with _sync_engine.connect() as connection:
        lifecycle = (
            connection.execute(
                text(
                    """
                SELECT removed_at, removed_by, removal_reason
                FROM ip_blacklist WHERE id = :id
                """
                ),
                {"id": entry_id},
            )
            .mappings()
            .one()
        )
    assert dict(lifecycle) == {
        "removed_at": None,
        "removed_by": None,
        "removal_reason": None,
    }


async def test_blacklist_list_excludes_expired_entries(
    client: AsyncClient, admin_token: str
):
    with _sync_engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO ip_blacklist (ip, reason, expires_at, created_at)
                VALUES (
                    '10.0.0.23', 'expired entry', now() - interval '1 second', now()
                )
                """
            )
        )

    listed = await client.get("/api/ip-blacklist", headers=_auth(admin_token))
    assert "10.0.0.23" not in {entry["ip"] for entry in listed.json()}


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
