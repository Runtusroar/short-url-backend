"""IP blacklist endpoint tests."""

import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from httpx import AsyncClient
from sqlalchemy import select, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import settings
from app.core.exceptions import ConflictError
from app.features.blacklist.schemas import IpBlacklistCreate
from app.features.blacklist.service import add_to_blacklist
from app.models import User
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


class _BlacklistSelectBarrier:
    """Pause exactly before each worker's real blacklist row-lock SELECT."""

    def __init__(self) -> None:
        self._identities: dict[str, int] = {}
        self.arrived = asyncio.Event()
        self.release = asyncio.Event()

    async def wait_before_execute(self, identity: tuple[str, int]) -> None:
        application_name, backend_pid = identity
        self._identities[application_name] = backend_pid
        if len(self._identities) == 2:
            self.arrived.set()
        await self.arrived.wait()
        await self.release.wait()

    @property
    def identities(self) -> dict[str, int]:
        return dict(self._identities)


def _is_blacklist_select_for_update(statement) -> bool:
    if getattr(statement, "_for_update_arg", None) is None:
        return False
    return any(
        getattr(from_clause, "name", None) == "ip_blacklist"
        for from_clause in statement.get_final_froms()
    )


@asynccontextmanager
async def _isolated_session_factory(barrier: _BlacklistSelectBarrier | None = None):
    """Create per-test real PostgreSQL connections for concurrency observations.

    pytest-asyncio creates a loop per module.  The application-level async pool
    can therefore retain connections from a previous module; NullPool keeps the
    observer, holder, and workers on fresh connections in this test's loop.
    """
    engine = create_async_engine(settings.database_url, poolclass=NullPool)

    if barrier is None:
        session_class = AsyncSession
    else:

        class BarrierSession(AsyncSession):
            async def execute(self, statement, *args, **kwargs):
                identity = getattr(self, "_blacklist_worker_identity", None)
                if identity is not None and _is_blacklist_select_for_update(statement):
                    await barrier.wait_before_execute(identity)
                return await super().execute(statement, *args, **kwargs)

        session_class = BarrierSession
    sessions = async_sessionmaker(engine, class_=session_class, expire_on_commit=False)
    try:
        yield sessions
    finally:
        await engine.dispose()


async def _staff_users(session_factory) -> tuple[User, User]:
    async with session_factory() as session:
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


def _test_identifier(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


def _quoted_identifier(identifier: str) -> str:
    return postgresql.dialect().identifier_preparer.quote(identifier)


async def _set_application_name(session, application_name: str) -> tuple[str, int]:
    await session.execute(
        text("SELECT set_config('application_name', :application_name, false)"),
        {"application_name": application_name},
    )
    backend_pid = await session.scalar(text("SELECT pg_backend_pid()"))
    identity = (application_name, backend_pid)
    session._blacklist_worker_identity = identity
    return identity


async def _wait_for_workers_to_block(
    session_factory, worker_identities: dict[str, int]
) -> None:
    first_pid, second_pid = worker_identities.values()
    async with session_factory() as observer:
        last_rows = []

        async def observe() -> None:
            nonlocal last_rows
            while True:
                rows = (
                    (
                        await observer.execute(
                            text(
                                """
                            SELECT pid, application_name, wait_event_type
                            FROM pg_stat_activity
                            WHERE pid IN (:first_pid, :second_pid)
                            """
                            ),
                            {
                                "first_pid": first_pid,
                                "second_pid": second_pid,
                            },
                        )
                    )
                    .mappings()
                    .all()
                )
                last_rows = [dict(row) for row in rows]
                if {(row["application_name"], row["pid"]) for row in rows} == set(
                    worker_identities.items()
                ) and all(row["wait_event_type"] == "Lock" for row in rows):
                    return
                await asyncio.sleep(0.01)

        try:
            await asyncio.wait_for(observe(), timeout=5)
        except TimeoutError as exc:
            raise AssertionError(
                "workers did not both block; "
                f"expected: {worker_identities!r}; last activity: {last_rows!r}"
            ) from exc


async def _release_holder(holder) -> None:
    if holder is None:
        return
    if holder.in_transaction():
        await holder.rollback()
    await holder.close()


async def _cancel_workers(workers: list[asyncio.Task]) -> None:
    for worker in workers:
        if not worker.done():
            worker.cancel()
    if workers:
        await asyncio.gather(*workers, return_exceptions=True)


async def _run_existing_reactivation_race(ip: str, session_factory, barrier):
    admin, operator = await _staff_users(session_factory)
    with _sync_engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO ip_blacklist (ip, reason, expires_at, created_at)
                VALUES (
                    CAST(:ip AS inet),
                    'expired',
                    now() - interval '1 second',
                    now()
                )
                """
            ),
            {"ip": ip},
        )

    async def reactivate(
        reason: str, user: User, application_name: str
    ) -> tuple[str, object]:
        async with session_factory() as session:
            try:
                await _set_application_name(session, application_name)
                entry = await add_to_blacklist(
                    session,
                    IpBlacklistCreate(ip=ip, reason=reason),
                    user,
                )
                return "success", entry
            except ConflictError as exc:
                return "conflict", exc

    holder = None
    worker_names = (
        _test_identifier("blacklist_worker"),
        _test_identifier("blacklist_worker"),
    )
    workers: list[asyncio.Task] = []
    try:
        holder = session_factory()
        await holder.execute(
            text("SELECT id FROM ip_blacklist WHERE ip = CAST(:ip AS inet) FOR UPDATE"),
            {"ip": ip},
        )
        workers = [
            asyncio.create_task(reactivate("winner admin", admin, worker_names[0])),
            asyncio.create_task(
                reactivate("winner operator", operator, worker_names[1])
            ),
        ]
        await asyncio.wait_for(barrier.arrived.wait(), timeout=5)
        worker_identities = barrier.identities
        assert set(worker_identities) == set(worker_names)
        assert len(set(worker_identities.values())) == 2
        barrier.release.set()
        await _wait_for_workers_to_block(session_factory, worker_identities)
        await _release_holder(holder)
        holder = None
        results = await asyncio.wait_for(asyncio.gather(*workers), timeout=5)
    finally:
        await _release_holder(holder)
        await _cancel_workers(workers)

    with _sync_engine.connect() as connection:
        row = (
            connection.execute(
                text(
                    """
                    SELECT host(ip) AS ip, reason, created_by, removed_at
                    FROM ip_blacklist WHERE ip = CAST(:ip AS inet)
                    """
                ),
                {"ip": ip},
            )
            .mappings()
            .one()
        )
    return results, row


async def test_concurrent_reactivation_locks_existing_canonical_ipv6_row():
    barrier = _BlacklistSelectBarrier()
    async with _isolated_session_factory(barrier) as session_factory:
        results, row = await _run_existing_reactivation_race(
            "2001:db8::24", session_factory, barrier
        )
    successes = [result for result in results if result[0] == "success"]
    conflicts = [result for result in results if result[0] == "conflict"]
    assert len(successes) == 1
    assert len(conflicts) == 1
    winning_entry = successes[0][1]
    assert row["ip"] == "2001:db8::24"
    assert row["reason"] == winning_entry.reason
    assert row["created_by"] == winning_entry.created_by
    assert row["removed_at"] is None


async def test_concurrent_new_blacklist_insert_returns_one_conflict_after_rollback():
    advisory_lock = uuid4().int & 0x7FFFFFFF
    function_name = _test_identifier("block_blacklist_insert")
    trigger_name = _test_identifier("block_blacklist_insert_trigger")
    quoted_function = _quoted_identifier(function_name)
    quoted_trigger = _quoted_identifier(trigger_name)

    holder = None
    workers: list[asyncio.Task] = []
    try:
        with _sync_engine.begin() as connection:
            connection.execute(
                text(
                    f"""
                    CREATE FUNCTION {quoted_function}() RETURNS trigger
                    LANGUAGE plpgsql AS $$
                    BEGIN
                        PERFORM pg_advisory_xact_lock({advisory_lock});
                        RETURN NEW;
                    END;
                    $$
                    """
                )
            )
            connection.execute(
                text(
                    f"""
                    CREATE TRIGGER {quoted_trigger}
                    BEFORE INSERT ON ip_blacklist
                    FOR EACH ROW EXECUTE FUNCTION {quoted_function}()
                    """
                )
            )
        async with _isolated_session_factory() as session_factory:
            admin, operator = await _staff_users(session_factory)
            holder = session_factory()
            await holder.execute(
                text("SELECT pg_advisory_xact_lock(:lock)"), {"lock": advisory_lock}
            )
            worker_names = (
                _test_identifier("blacklist_worker"),
                _test_identifier("blacklist_worker"),
            )
            worker_identities: asyncio.Queue[tuple[str, int]] = asyncio.Queue()

            async def create_with_name(reason: str, user: User, application_name: str):
                async with session_factory() as session:
                    try:
                        identity = await _set_application_name(
                            session, application_name
                        )
                        await worker_identities.put(identity)
                        entry = await add_to_blacklist(
                            session,
                            IpBlacklistCreate(ip="2001:db8::25", reason=reason),
                            user,
                        )
                        return "success", entry
                    except ConflictError as exc:
                        return "conflict", exc

            workers = [
                asyncio.create_task(
                    create_with_name("new winner admin", admin, worker_names[0])
                ),
                asyncio.create_task(
                    create_with_name("new winner operator", operator, worker_names[1])
                ),
            ]
            identities = {
                name: pid
                for name, pid in (
                    await asyncio.wait_for(worker_identities.get(), timeout=5),
                    await asyncio.wait_for(worker_identities.get(), timeout=5),
                )
            }
            assert set(identities) == set(worker_names)
            assert len(set(identities.values())) == 2
            await _wait_for_workers_to_block(session_factory, identities)
            await _release_holder(holder)
            holder = None
            results = await asyncio.wait_for(asyncio.gather(*workers), timeout=5)
    finally:
        await _release_holder(holder)
        await _cancel_workers(workers)
        with _sync_engine.begin() as connection:
            connection.execute(
                text(f"DROP TRIGGER IF EXISTS {quoted_trigger} ON ip_blacklist")
            )
            connection.execute(text(f"DROP FUNCTION IF EXISTS {quoted_function}()"))
            assert (
                connection.execute(
                    text(
                        """
                    SELECT count(*) FROM pg_locks
                    WHERE locktype = 'advisory' AND granted AND objid = :lock
                    """
                    ),
                    {"lock": advisory_lock},
                ).scalar_one()
                == 0
            )
            assert (
                connection.execute(
                    text(
                        "SELECT count(*) FROM pg_trigger WHERE tgname = :trigger_name"
                    ),
                    {"trigger_name": trigger_name},
                ).scalar_one()
                == 0
            )
            assert (
                connection.execute(
                    text("SELECT count(*) FROM pg_proc WHERE proname = :function_name"),
                    {"function_name": function_name},
                ).scalar_one()
                == 0
            )
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
