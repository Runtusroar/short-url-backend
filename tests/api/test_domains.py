import uuid

import pytest
from sqlalchemy import text

from app.db import AsyncSessionLocal


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_authenticated_domain_listing_is_active_and_scoped(client, admin_token, read_token, domain_a):
    admin = await client.get("/api/domains", headers=auth(admin_token))
    assert admin.status_code == 200, admin.text
    assert {item["name"] for item in admin.json()["items"]} >= {"test.local", "second.test.local"}

    reader = await client.get("/api/domains", headers=auth(read_token))
    assert reader.status_code == 200, reader.text
    assert [item["id"] for item in reader.json()["items"]] == [str(domain_a.id)]
    assert reader.json()["items"][0]["is_active"] is True


@pytest.mark.asyncio
async def test_admin_can_create_update_and_delete_domain(client, admin_token):
    name = f"{uuid.uuid4().hex[:16]}.example.test"
    created = await client.post("/api/domains", headers=auth(admin_token), json={"name": name})
    assert created.status_code == 201, created.text
    assert created.json()["name"] == name

    updated = await client.put(
        f"/api/domains/{created.json()['id']}", headers=auth(admin_token), json={"is_active": False}
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["is_active"] is False

    deleted = await client.delete(f"/api/domains/{created.json()['id']}", headers=auth(admin_token))
    assert deleted.status_code == 204, deleted.text


@pytest.mark.asyncio
async def test_domain_mutation_requires_admin_and_domain_validation_is_strict(
    client, operator_token, admin_token
):
    denied = await client.post("/api/domains", headers=auth(operator_token), json={"name": "denied.test"})
    assert denied.status_code == 403
    assert denied.json()["code"] == "PERMISSION_DENIED"

    for invalid_name in ("https://example.test", "example.test/path", "example.test:8443", "example.test?q=1"):
        invalid = await client.post(
            "/api/domains", headers=auth(admin_token), json={"name": invalid_name}
        )
        assert invalid.status_code == 422
        assert invalid.json()["code"] == "VALIDATION_ERROR"


@pytest.mark.asyncio
async def test_subaccounts_cannot_update_or_delete_domains(client, admin_token, operator_token):
    created = await client.post(
        "/api/domains",
        headers=auth(admin_token),
        json={"name": f"{uuid.uuid4().hex[:16]}.protected.test"},
    )
    assert created.status_code == 201, created.text

    update = await client.put(
        f"/api/domains/{created.json()['id']}", headers=auth(operator_token), json={"is_active": False}
    )
    assert update.status_code == 403
    delete = await client.delete(
        f"/api/domains/{created.json()['id']}", headers=auth(operator_token)
    )
    assert delete.status_code == 403


@pytest.mark.asyncio
async def test_domain_conflict_missing_and_referenced_domain_cannot_be_deleted(client, admin_token):
    duplicate = await client.post("/api/domains", headers=auth(admin_token), json={"name": "test.local"})
    assert duplicate.status_code == 409
    assert duplicate.json()["code"] == "DOMAIN_CONFLICT"

    missing = await client.put(
        f"/api/domains/{uuid.uuid4()}", headers=auth(admin_token), json={"is_active": False}
    )
    assert missing.status_code == 404

    referenced_name = f"{uuid.uuid4().hex[:16]}.reference.test"
    domain = await client.post("/api/domains", headers=auth(admin_token), json={"name": referenced_name})
    assert domain.status_code == 201, domain.text
    async with AsyncSessionLocal() as session:
        await session.execute(
            text(
                """
                INSERT INTO short_links (id, domain_id, short_code, is_custom_alias, is_active, created_at, updated_at)
                VALUES (:id, :domain_id, :short_code, false, true, now(), now())
                """
            ),
            {
                "id": str(uuid.uuid4()),
                "domain_id": domain.json()["id"],
                "short_code": uuid.uuid4().hex[:8],
            },
        )
        await session.commit()

    blocked = await client.delete(f"/api/domains/{domain.json()['id']}", headers=auth(admin_token))
    assert blocked.status_code == 409
    assert blocked.json()["code"] == "DOMAIN_IN_USE"
