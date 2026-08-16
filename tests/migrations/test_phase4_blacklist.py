"""Database contract for the blacklist lifecycle revision."""

import uuid
from subprocess import CalledProcessError

import pytest
from sqlalchemy import create_engine, inspect, text

from tests.migrations.support import run_alembic

BASE_REVISION = "de4c81b75920"
REVISION = "e52d9a6c8031"


def _seed_user(connection, suffix: str) -> uuid.UUID:
    user_id = uuid.uuid4()
    connection.execute(
        text(
            """
            INSERT INTO users (
                id, username, password_hash, role, is_active, created_at, updated_at
            )
            VALUES (:id, :username, 'hash', 'admin', true, now(), now())
            """
        ),
        {"id": user_id, "username": f"blacklist-{suffix}"},
    )
    return user_id


def test_blacklist_migrates_to_inet_lifecycle_and_round_trips(migration_database_url):
    run_alembic(migration_database_url, "upgrade", BASE_REVISION)
    engine = create_engine(migration_database_url)
    try:
        with engine.begin() as connection:
            user_id = _seed_user(connection, "contract")
            connection.execute(
                text(
                    """
                    INSERT INTO ip_blacklist (id, ip, reason, created_by, created_at)
                    VALUES
                        (:v4_id, '192.0.2.1', NULL, :user_id, now()),
                        (:v6_id, '2001:0db8:0:0:0:0:0:1', '  ', :user_id, now())
                    """
                ),
                {"v4_id": uuid.uuid4(), "v6_id": uuid.uuid4(), "user_id": user_id},
            )

        run_alembic(migration_database_url, "upgrade", REVISION)

        with engine.begin() as connection:
            rows = (
                connection.execute(
                    text(
                        "SELECT host(ip) AS ip, reason FROM ip_blacklist "
                        "ORDER BY host(ip)"
                    )
                )
                .mappings()
                .all()
            )
            assert rows == [
                {"ip": "192.0.2.1", "reason": "Legacy blacklist entry"},
                {"ip": "2001:db8::1", "reason": "Legacy blacklist entry"},
            ]
            inserted = (
                connection.execute(
                    text(
                        """
                    INSERT INTO ip_blacklist (ip, reason)
                    VALUES ('2001:db8::2', 'direct insert')
                    RETURNING id, created_at
                    """
                    )
                )
                .mappings()
                .one()
            )
            assert inserted["id"] is not None
            assert inserted["created_at"] is not None

        inspector = inspect(engine)
        columns = {
            column["name"]: column for column in inspector.get_columns("ip_blacklist")
        }
        assert columns["ip"]["type"].__class__.__name__ == "INET"
        assert columns["reason"]["nullable"] is False
        assert {
            "expires_at",
            "removed_at",
            "removed_by",
            "removal_reason",
        } <= columns.keys()
        assert "gen_random_uuid" in columns["id"]["default"]
        assert "now()" in columns["created_at"]["default"]
        checks = {
            check["name"] for check in inspector.get_check_constraints("ip_blacklist")
        }
        assert {"ck_ip_blacklist_reason", "ck_ip_blacklist_removed_actor"} <= checks
        foreign_keys = {
            foreign_key["constrained_columns"][0]: foreign_key
            for foreign_key in inspector.get_foreign_keys("ip_blacklist")
        }
        assert foreign_keys["created_by"]["name"] == "fk_ip_blacklist_created_by"
        assert foreign_keys["created_by"]["options"]["ondelete"] == "RESTRICT"
        assert foreign_keys["removed_by"]["name"] == "fk_ip_blacklist_removed_by"
        assert foreign_keys["removed_by"]["options"]["ondelete"] == "RESTRICT"
        indexes = {index["name"] for index in inspector.get_indexes("ip_blacklist")}
        assert "idx_ip_blacklist_active_expires_at" in indexes

        run_alembic(migration_database_url, "downgrade", BASE_REVISION)
        inspector = inspect(engine)
        downgraded = {
            column["name"]: column for column in inspector.get_columns("ip_blacklist")
        }
        assert downgraded["ip"]["type"].__class__.__name__ == "VARCHAR"
        assert {"expires_at", "removed_at", "removed_by", "removal_reason"}.isdisjoint(
            downgraded
        )
        assert downgraded["id"]["default"] is not None
        assert downgraded["created_at"]["default"] is None
        foreign_keys = {
            foreign_key["constrained_columns"][0]: foreign_key
            for foreign_key in inspector.get_foreign_keys("ip_blacklist")
        }
        assert foreign_keys["created_by"]["name"] == "ip_blacklist_created_by_fkey"
        assert foreign_keys["created_by"]["options"].get("ondelete") is None
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO ip_blacklist (ip, reason, created_at)
                    VALUES ('203.0.113.9', NULL, now())
                    """
                )
            )
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    ("ips", "invariant"),
    [
        (["not-an-ip"], "ip blacklist IP invariant"),
        (
            ["2001:0db8:0:0:0:0:0:1", "2001:db8::1"],
            "canonical uniqueness invariant",
        ),
    ],
)
def test_blacklist_migration_rejects_invalid_or_canonical_duplicate_addresses(
    migration_database_url, ips, invariant
):
    run_alembic(migration_database_url, "upgrade", BASE_REVISION)
    engine = create_engine(migration_database_url)
    try:
        with engine.begin() as connection:
            for ip in ips:
                connection.execute(
                    text(
                        """
                        INSERT INTO ip_blacklist (ip, reason, created_at)
                        VALUES (:ip, NULL, now())
                        """
                    ),
                    {"ip": ip},
                )

        with pytest.raises(CalledProcessError) as exc_info:
            run_alembic(migration_database_url, "upgrade", REVISION)
        output = f"{exc_info.value.stdout}\n{exc_info.value.stderr}"
        assert invariant in output
        assert all(ip not in output for ip in ips)

        with engine.connect() as connection:
            assert (
                connection.execute(
                    text(
                        "SELECT count(*) FROM alembic_version "
                        "WHERE version_num = :revision"
                    ),
                    {"revision": REVISION},
                ).scalar_one()
                == 0
            )
            reasons = (
                connection.execute(text("SELECT reason FROM ip_blacklist"))
                .scalars()
                .all()
            )
            assert reasons == [None] * len(ips)
    finally:
        engine.dispose()
