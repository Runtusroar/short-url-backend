"""Database contract for the short-link, target, and permission lifecycle revision."""

import uuid
from subprocess import CalledProcessError

import pytest
from sqlalchemy import create_engine, inspect, text

from tests.migrations.support import run_alembic


BASE_REVISION = "f31a8c0d4e72"
REVISION = "9b6d2f4a7c11"


def _seed_legacy_link(connection, *, description: str | None, default_action: str = "allow"):
    user_id = uuid.uuid4()
    domain_id = uuid.uuid4()
    link_id = uuid.uuid4()
    connection.execute(
        text(
            """
            INSERT INTO users (id, username, password_hash, role, is_active, created_at, updated_at)
            VALUES (:id, :username, 'hash', 'admin', true, now(), now())
            """
        ),
        {"id": user_id, "username": f"legacy-{user_id.hex[:16]}"},
    )
    connection.execute(
        text(
            """
            INSERT INTO domains (id, name, timezone, is_active, is_default, created_at, updated_at)
            VALUES (:id, :name, 'Asia/Shanghai', true, false, now(), now())
            """
        ),
        {"id": domain_id, "name": f"legacy-{domain_id.hex[:16]}.example.com"},
    )
    connection.execute(
        text(
            """
            INSERT INTO short_links
                (id, domain_id, short_code, is_custom_alias, description, owner_id,
                 is_active, default_action, created_at, updated_at)
            VALUES (:id, :domain_id, :short_code, false, :description, :owner_id,
                    true, :default_action, now(), now())
            """
        ),
        {
            "id": link_id,
            "domain_id": domain_id,
            "short_code": f"code{link_id.hex[:8]}",
            "description": description,
            "owner_id": user_id,
            "default_action": default_action,
        },
    )
    return user_id, domain_id, link_id


def test_short_link_lifecycle_migrates_legacy_rows_and_round_trips(migration_database_url):
    run_alembic(migration_database_url, "upgrade", BASE_REVISION)
    engine = create_engine(migration_database_url)
    try:
        with engine.begin() as connection:
            user_id, domain_id, blank_link_id = _seed_legacy_link(
                connection, description="   "
            )
            _, _, named_link_id = _seed_legacy_link(
                connection, description="Legacy campaign", default_action="allow"
            )
            connection.execute(
                text(
                    """
                    INSERT INTO target_urls
                        (id, short_link_id, url, url_type, weight, is_active, created_at)
                    VALUES (:id, :short_link_id, 'https://legacy.example.com', 'allowed', 0, true, now())
                    """
                ),
                {"id": uuid.uuid4(), "short_link_id": blank_link_id},
            )
            connection.execute(
                text(
                    """
                    INSERT INTO short_link_permissions (id, short_link_id, user_id, created_at)
                    VALUES (:id, :short_link_id, :user_id, now())
                    """
                ),
                {"id": uuid.uuid4(), "short_link_id": blank_link_id, "user_id": user_id},
            )

        run_alembic(migration_database_url, "upgrade", REVISION)

        with engine.connect() as connection:
            assert connection.scalar(
                text("SELECT name FROM short_links WHERE id = :id"), {"id": blank_link_id}
            ) == f"code{blank_link_id.hex[:8]}"
            assert connection.scalar(
                text("SELECT name FROM short_links WHERE id = :id"), {"id": named_link_id}
            ) == "Legacy campaign"
            assert connection.scalar(
                text("SELECT default_action FROM short_links WHERE id = :id"), {"id": named_link_id}
            ) == "allow"
            assert connection.scalar(
                text("SELECT weight FROM target_urls WHERE short_link_id = :id"), {"id": blank_link_id}
            ) == 1
            assert connection.scalar(
                text("SELECT is_active FROM target_urls WHERE short_link_id = :id"), {"id": blank_link_id}
            ) is False
            assert connection.scalar(
                text("SELECT granted_by FROM short_link_permissions WHERE short_link_id = :id"),
                {"id": blank_link_id},
            ) is None
            assert connection.scalar(
                text("SELECT column_default FROM information_schema.columns WHERE table_name = 'short_links' AND column_name = 'default_action'"),
            ) == "'deny'::character varying"

        inspector = inspect(engine)
        short_link_checks = {check["name"] for check in inspector.get_check_constraints("short_links")}
        assert {"ck_short_links_default_action", "ck_short_links_deleted_actor"} <= short_link_checks
        target_checks = {check["name"] for check in inspector.get_check_constraints("target_urls")}
        assert {"ck_target_urls_type", "ck_target_urls_weight"} <= target_checks
        short_link_columns = {column["name"]: column for column in inspector.get_columns("short_links")}
        assert "name" in short_link_columns
        assert "description" not in short_link_columns
        assert short_link_columns["name"]["nullable"] is False
        assert "now()" in short_link_columns["created_at"]["default"]
        assert "now()" in short_link_columns["updated_at"]["default"]
        assert "true" in short_link_columns["is_active"]["default"]
        assert "false" in short_link_columns["is_custom_alias"]["default"]
        target_columns = {column["name"]: column for column in inspector.get_columns("target_urls")}
        assert "name" in target_columns
        assert "updated_at" in target_columns
        assert "now()" in target_columns["updated_at"]["default"]
        indexes = {index["name"]: index for index in inspector.get_indexes("short_links")}
        assert {
            "idx_short_links_domain",
            "idx_short_links_domain_created_at",
            "idx_short_links_domain_owner_created_at",
        } <= indexes.keys()
        assert indexes["idx_short_links_domain_created_at"]["column_names"] == ["domain_id", "created_at"]
        assert indexes["idx_short_links_domain_owner_created_at"]["column_names"] == ["domain_id", "owner_id", "created_at"]
        short_link_fks = {fk["constrained_columns"][0]: fk for fk in inspector.get_foreign_keys("short_links")}
        assert short_link_fks["domain_id"]["options"]["ondelete"] == "RESTRICT"
        assert short_link_fks["owner_id"]["options"]["ondelete"] == "RESTRICT"
        assert short_link_fks["deleted_by"]["options"]["ondelete"] == "RESTRICT"
        permission_fks = {fk["constrained_columns"][0]: fk for fk in inspector.get_foreign_keys("short_link_permissions")}
        assert permission_fks["short_link_id"]["options"]["ondelete"] == "RESTRICT"
        assert permission_fks["user_id"]["options"]["ondelete"] == "RESTRICT"
        assert permission_fks["granted_by"]["options"]["ondelete"] == "RESTRICT"

        run_alembic(migration_database_url, "downgrade", BASE_REVISION)
        with engine.connect() as connection:
            assert connection.scalar(
                text("SELECT description FROM short_links WHERE id = :id"), {"id": named_link_id}
            ) == "Legacy campaign"
        downgraded_columns = {column["name"] for column in inspect(engine).get_columns("short_links")}
        assert "description" in downgraded_columns
        assert "name" not in downgraded_columns
    finally:
        engine.dispose()


def test_short_link_lifecycle_rejects_overlong_legacy_name_before_recording_revision(
    migration_database_url,
):
    run_alembic(migration_database_url, "upgrade", BASE_REVISION)
    engine = create_engine(migration_database_url)
    secret_description = "x" * 129
    try:
        with engine.begin() as connection:
            _seed_legacy_link(connection, description=secret_description)
        with pytest.raises(CalledProcessError) as error:
            run_alembic(migration_database_url, "upgrade", REVISION)
        assert secret_description not in str(error.value)
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == BASE_REVISION
            assert connection.scalar(
                text(
                    "SELECT count(*) FROM information_schema.columns "
                    "WHERE table_name = 'short_links' AND column_name = 'name'"
                )
            ) == 0
    finally:
        engine.dispose()
