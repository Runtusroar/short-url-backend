import asyncio
import uuid

import pytest
from sqlalchemy import event

from app.db import engine


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_admin_can_create_list_update_and_delete_global_ip_blacklist_entry(client, admin_token):
    ip = f"203.0.113.{int(uuid.uuid4().hex[:2], 16) % 250 + 1}"
    created = await client.post(
        "/api/security/ip-blacklist", headers=auth(admin_token), json={"ip": ip, "reason": "integration test"}
    )
    assert created.status_code == 201, created.text
    entry = created.json()
    assert entry["ip"] == ip
    assert entry["created_by_username"] == "admin"

    listed = await client.get("/api/security/ip-blacklist", headers=auth(admin_token))
    assert listed.status_code == 200, listed.text
    assert any(item["id"] == entry["id"] for item in listed.json()["items"])

    updated = await client.put(
        f"/api/security/ip-blacklist/{entry['id']}",
        headers=auth(admin_token),
        json={"ip": "2001:db8::1", "reason": "updated"},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["ip"] == "2001:db8::1"
    assert updated.json()["reason"] == "updated"

    deleted = await client.delete(f"/api/security/ip-blacklist/{entry['id']}", headers=auth(admin_token))
    assert deleted.status_code == 204, deleted.text


@pytest.mark.asyncio
async def test_explicit_null_blacklist_ip_is_a_noop(client, admin_token):
    created = await client.post(
        "/api/security/ip-blacklist",
        headers=auth(admin_token),
        json={"ip": f"198.51.100.{int(uuid.uuid4().hex[:2], 16) % 250 + 1}"},
    )
    assert created.status_code == 201, created.text

    updated = await client.put(
        f"/api/security/ip-blacklist/{created.json()['id']}",
        headers=auth(admin_token),
        json={"ip": None},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["ip"] == created.json()["ip"]


@pytest.mark.asyncio
async def test_global_blacklist_requires_admin_authenticates_and_normalizes_exact_ips(client, admin_token, operator_token):
    unauthenticated = await client.get("/api/security/ip-blacklist")
    assert unauthenticated.status_code == 401

    denied = await client.post(
        "/api/security/ip-blacklist", headers=auth(operator_token), json={"ip": "203.0.113.9"}
    )
    assert denied.status_code == 403

    invalid = await client.post(
        "/api/security/ip-blacklist", headers=auth(admin_token), json={"ip": "203.0.113.0/24"}
    )
    assert invalid.status_code == 422
    assert invalid.json()["code"] == "VALIDATION_ERROR"

    scoped_ipv6 = await client.post(
        "/api/security/ip-blacklist", headers=auth(admin_token), json={"ip": "fe80::1%eth0"}
    )
    assert scoped_ipv6.status_code == 422
    assert scoped_ipv6.json()["code"] == "VALIDATION_ERROR"
    assert scoped_ipv6.json()["details"]

    first = await client.post(
        "/api/security/ip-blacklist", headers=auth(admin_token), json={"ip": "2001:0db8:0:0:0:0:0:9"}
    )
    assert first.status_code == 201, first.text
    duplicate = await client.post(
        "/api/security/ip-blacklist", headers=auth(admin_token), json={"ip": "2001:db8::9"}
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["code"] == "IP_BLACKLIST_CONFLICT"

    missing = await client.put(
        f"/api/security/ip-blacklist/{uuid.uuid4()}", headers=auth(admin_token), json={"reason": "missing"}
    )
    assert missing.status_code == 404
    assert missing.json()["details"] is None


@pytest.mark.asyncio
async def test_concurrent_blacklist_ips_return_a_named_conflict(client, admin_token):
    """Exact-IP uniqueness remains a stable API contract when both requests pass the preflight read."""
    ip = f"198.51.100.{int(uuid.uuid4().hex[:2], 16) % 250 + 1}"
    statements: list[str] = []

    def record_statement(_, __, statement, ___, ____, _____):
        statements.append(statement.lower())

    event.listen(engine.sync_engine, "before_cursor_execute", record_statement)
    try:
        first, second = await asyncio.gather(
            client.post("/api/security/ip-blacklist", headers=auth(admin_token), json={"ip": ip}),
            client.post("/api/security/ip-blacklist", headers=auth(admin_token), json={"ip": ip}),
        )
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", record_statement)

    assert sorted(response.status_code for response in (first, second)) == [201, 409]
    conflict = next(response for response in (first, second) if response.status_code == 409)
    assert conflict.json()["code"] == "IP_BLACKLIST_CONFLICT"
    assert sum("insert into ip_blacklist" in statement for statement in statements) == 2
    assert not any(
        "from ip_blacklist" in statement and "where ip_blacklist.ip" in statement
        for statement in statements
    )


@pytest.mark.asyncio
async def test_subaccounts_cannot_update_or_delete_blacklist_entries(client, admin_token, operator_token):
    created = await client.post(
        "/api/security/ip-blacklist",
        headers=auth(admin_token),
        json={"ip": f"198.51.100.{int(uuid.uuid4().hex[:2], 16) % 250 + 1}"},
    )
    assert created.status_code == 201, created.text

    update = await client.put(
        f"/api/security/ip-blacklist/{created.json()['id']}",
        headers=auth(operator_token),
        json={"reason": "denied"},
    )
    assert update.status_code == 403
    delete = await client.delete(
        f"/api/security/ip-blacklist/{created.json()['id']}", headers=auth(operator_token)
    )
    assert delete.status_code == 403
