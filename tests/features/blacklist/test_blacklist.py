"""IP blacklist endpoint tests."""

import asyncio
from datetime import UTC, datetime, timedelta

from httpx import AsyncClient
from sqlalchemy import select, text

from app.core.database import AsyncSessionLocal
from app.core.exceptions import ConflictError
from app.features.blacklist.schemas import IpBlacklistCreate
from app.features.blacklist.service import add_to_blacklist
from app.models import IpBlacklist, User
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


async def test_blacklist_remove_is_idempotent_and_preserves_first_actor_history(
    client: AsyncClient, admin_token: str, operator_token: str
):
    created = await client.post(
        "/api/ip-blacklist",
        headers=_auth(admin_token),
        json={"ip": "10.0.0.24", "reason": "preserve removal"},
    )
    assert created.status_code == 200
    entry_id = created.json()["id"]

    first_remove = await client.delete(
        f"/api/ip-blacklist/{entry_id}", headers=_auth(admin_token)
    )
    assert first_remove.status_code == 200
    with _sync_engine.connect() as connection:
        first_history = (
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

    second_remove = await client.delete(
        f"/api/ip-blacklist/{entry_id}", headers=_auth(operator_token)
    )
    assert second_remove.status_code == 200
    with _sync_engine.connect() as connection:
        second_history = (
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
    assert dict(second_history) == dict(first_history)


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


async def _staff_users() -> tuple[User, User]:
    async with AsyncSessionLocal() as session:
        users = (
            (
                await session.execute(
                    select(User).where(User.username.in_(["admin", "operator"]))
                )
            )
            .scalars()
            .all()
        )
    users_by_name = {user.username: user for user in users}
    return users_by_name["admin"], users_by_name["operator"]


async def test_concurrent_reactivation_locks_existing_canonical_ipv6_row():
    admin, operator = await _staff_users()
    with _sync_engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO ip_blacklist (ip, reason, expires_at, created_at)
                VALUES (
                    '2001:0db8:0:0:0:0:0:24',
                    'expired',
                    now() - interval '1 second',
                    now()
                )
                """
            )
        )

    async def reactivate(reason: str, user: User) -> tuple[str, object]:
        async with AsyncSessionLocal() as session:
            try:
                entry = await add_to_blacklist(
                    session,
                    IpBlacklistCreate(ip="2001:db8::24", reason=reason),
                    user,
                )
                return "success", entry
            except ConflictError as exc:
                return "conflict", exc

    async with AsyncSessionLocal() as lock_session:
        await lock_session.execute(
            select(IpBlacklist)
            .where(IpBlacklist.ip.op("=")(text("'2001:db8::24'::inet")))
            .with_for_update()
        )
        first = asyncio.create_task(reactivate("winner admin", admin))
        second = asyncio.create_task(reactivate("winner operator", operator))
        await asyncio.sleep(0.1)
        assert not first.done()
        assert not second.done()
        await lock_session.commit()

    results = await asyncio.gather(first, second)
    successes = [result for result in results if result[0] == "success"]
    conflicts = [result for result in results if result[0] == "conflict"]
    assert len(successes) == 1
    assert len(conflicts) == 1
    winning_entry = successes[0][1]
    with _sync_engine.connect() as connection:
        row = (
            connection.execute(
                text(
                    """
                SELECT host(ip) AS ip, reason, created_by, removed_at
                FROM ip_blacklist WHERE ip = '2001:db8::24'::inet
                """
                )
            )
            .mappings()
            .one()
        )
    assert row["ip"] == "2001:db8::24"
    assert row["reason"] == winning_entry.reason
    assert row["created_by"] == winning_entry.created_by
    assert row["removed_at"] is None


async def test_concurrent_new_blacklist_insert_returns_one_conflict_after_rollback():
    admin, operator = await _staff_users()
    advisory_lock = 402481
    with _sync_engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE OR REPLACE FUNCTION block_blacklist_insert() RETURNS trigger
                LANGUAGE plpgsql AS $$
                BEGIN
                    PERFORM pg_advisory_xact_lock(402481);
                    RETURN NEW;
                END;
                $$
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TRIGGER block_blacklist_insert_before_write
                BEFORE INSERT ON ip_blacklist
                FOR EACH ROW EXECUTE FUNCTION block_blacklist_insert()
                """
            )
        )

    async def create(reason: str, user: User) -> tuple[str, object]:
        async with AsyncSessionLocal() as session:
            try:
                entry = await add_to_blacklist(
                    session,
                    IpBlacklistCreate(ip="2001:db8::25", reason=reason),
                    user,
                )
                return "success", entry
            except ConflictError as exc:
                return "conflict", exc

    async with AsyncSessionLocal() as lock_session:
        await lock_session.execute(
            text("SELECT pg_advisory_lock(:lock)"), {"lock": advisory_lock}
        )
        first = asyncio.create_task(create("new winner admin", admin))
        second = asyncio.create_task(create("new winner operator", operator))
        await asyncio.sleep(0.1)
        assert not first.done()
        assert not second.done()
        await lock_session.execute(
            text("SELECT pg_advisory_unlock(:lock)"), {"lock": advisory_lock}
        )
        await lock_session.commit()

    try:
        results = await asyncio.gather(first, second)
    finally:
        with _sync_engine.begin() as connection:
            connection.execute(
                text("DROP TRIGGER block_blacklist_insert_before_write ON ip_blacklist")
            )
            connection.execute(text("DROP FUNCTION block_blacklist_insert()"))
    assert sorted(result[0] for result in results) == ["conflict", "success"]


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
