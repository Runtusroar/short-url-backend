import asyncio
import uuid

import pytest
from sqlalchemy import event, select, text

from app.db import AsyncSessionLocal, engine
from app.db.models import Domain


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_authenticated_domain_listing_keeps_inactive_domains_admin_visible_and_subaccounts_scoped(client, admin_token, read_token, domain_a):
    admin = await client.get("/api/domains", headers=auth(admin_token))
    assert admin.status_code == 200, admin.text
    assert {item["name"] for item in admin.json()["items"]} >= {"test.local", "second.test.local"}

    ungranted = await client.post(
        "/api/domains",
        headers=auth(admin_token),
        json={"name": f"{uuid.uuid4().hex[:16]}.ungranted.test"},
    )
    assert ungranted.status_code == 201, ungranted.text

    reader = await client.get("/api/domains", headers=auth(read_token))
    assert reader.status_code == 200, reader.text
    listed_ids = {item["id"] for item in reader.json()["items"]}
    assert str(domain_a.id) in listed_ids
    assert ungranted.json()["id"] not in listed_ids
    assert all(item["is_active"] is True for item in reader.json()["items"])


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
async def test_domain_delete_is_idempotent(client, admin_token):
    """Retrying a completed deactivation must preserve the same audit identity and success result."""
    created = await client.post(
        "/api/domains", headers=auth(admin_token), json={"name": f"{uuid.uuid4().hex[:16]}.retry.test"}
    )
    assert created.status_code == 201, created.text
    domain_id = created.json()["id"]

    for _ in range(2):
        response = await client.delete(f"/api/domains/{domain_id}", headers=auth(admin_token))
        assert response.status_code == 204, response.text

    domains = await client.get("/api/domains", headers=auth(admin_token))
    retained = next(item for item in domains.json()["items"] if item["id"] == domain_id)
    assert retained["is_active"] is False


@pytest.mark.asyncio
async def test_concurrent_domain_deletes_serialize_on_the_postgresql_row_lock(client, admin_token):
    """The second real API delete must wait on the row lock and still complete idempotently."""
    created = await client.post(
        "/api/domains", headers=auth(admin_token), json={"name": f"{uuid.uuid4().hex[:16]}.lock.test"}
    )
    assert created.status_code == 201, created.text
    domain_id = uuid.UUID(created.json()["id"])
    lock_query_seen = asyncio.Event()
    loop = asyncio.get_running_loop()

    def record_lock_query(_, __, statement, ___, ____, _____):
        if "from domains" in statement.lower() and "for update" in statement.lower():
            loop.call_soon_threadsafe(lock_query_seen.set)

    delete_task = None
    listener_attached = False
    try:
        async with AsyncSessionLocal() as lock_session:
            async with lock_session.begin():
                await lock_session.scalar(select(Domain).where(Domain.id == domain_id).with_for_update())
                event.listen(engine.sync_engine, "before_cursor_execute", record_lock_query)
                listener_attached = True
                delete_task = asyncio.create_task(
                    client.delete(f"/api/domains/{domain_id}", headers=auth(admin_token))
                )
                await asyncio.wait_for(lock_query_seen.wait(), timeout=1)
                assert not delete_task.done()
        response = await asyncio.wait_for(delete_task, timeout=1)
    finally:
        if listener_attached:
            event.remove(engine.sync_engine, "before_cursor_execute", record_lock_query)

    assert response.status_code == 204, response.text


@pytest.mark.asyncio
async def test_domain_create_and_update_return_the_canonical_dns_name(client, admin_token):
    """Admin responses must expose the same normalized tenant key routing uses."""
    created = await client.post(
        "/api/domains", headers=auth(admin_token), json={"name": "Example.COM."}
    )
    assert created.status_code == 201, created.text
    assert created.json()["name"] == "example.com"

    updated = await client.put(
        f"/api/domains/{created.json()['id']}",
        headers=auth(admin_token),
        json={"name": "Other.Example.COM."},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["name"] == "other.example.com"


@pytest.mark.asyncio
async def test_concurrent_domain_names_return_a_named_conflict(client, admin_token):
    """Concurrent writes must classify PostgreSQL's named unique violation, not leak a generic conflict."""
    payload = {"name": f"{uuid.uuid4().hex[:16]}.concurrent.test"}
    first, second = await asyncio.gather(
        client.post("/api/domains", headers=auth(admin_token), json=payload),
        client.post("/api/domains", headers=auth(admin_token), json=payload),
    )

    assert sorted(response.status_code for response in (first, second)) == [201, 409]
    conflict = next(response for response in (first, second) if response.status_code == 409)
    assert conflict.json()["code"] == "DOMAIN_CONFLICT"


@pytest.mark.asyncio
async def test_explicit_null_domain_name_is_a_noop(client, admin_token):
    created = await client.post(
        "/api/domains",
        headers=auth(admin_token),
        json={"name": f"{uuid.uuid4().hex[:16]}.nullable.test"},
    )
    assert created.status_code == 201, created.text

    updated = await client.put(
        f"/api/domains/{created.json()['id']}", headers=auth(admin_token), json={"name": None}
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["name"] == created.json()["name"]


@pytest.mark.asyncio
async def test_domain_mutation_requires_admin_and_domain_validation_is_strict(
    client, operator_token, admin_token
):
    denied = await client.post("/api/domains", headers=auth(operator_token), json={"name": "denied.test"})
    assert denied.status_code == 403
    assert denied.json()["code"] == "PERMISSION_DENIED"

    for invalid_name in (
        "https://example.test",
        "example.test/path",
        "example.test:8443",
        "example.test?q=1",
        "K.example",
        " example.test",
    ):
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
async def test_domain_conflict_missing_and_referenced_domain_is_soft_deleted(client, admin_token):
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

    deleted = await client.delete(f"/api/domains/{domain.json()['id']}", headers=auth(admin_token))
    assert deleted.status_code == 204, deleted.text

    domains = await client.get("/api/domains", headers=auth(admin_token))
    retained = next(item for item in domains.json()["items"] if item["id"] == domain.json()["id"])
    assert retained["is_active"] is False
