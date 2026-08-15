"""Database contract for the user, domain, and grant lifecycle revision."""

import uuid
from subprocess import CalledProcessError

import pytest
from sqlalchemy import create_engine, inspect, text

from tests.migrations.support import run_alembic


BASE_REVISION = "d6e8f0a21b35"
REVISION = "f31a8c0d4e72"


def test_user_domain_lifecycle_rejects_invalid_legacy_host_without_leaking_value(
    migration_database_url,
):
    run_alembic(migration_database_url, "upgrade", BASE_REVISION)
    engine = create_engine(migration_database_url)
    invalid_name = f"{'a' * 64}.example.com"
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO domains (id, name, is_active, is_default, created_at)
                    VALUES (:id, :name, true, false, now())
                    """
                ),
                {"id": uuid.uuid4(), "name": invalid_name},
            )
        with pytest.raises(CalledProcessError) as error:
            run_alembic(migration_database_url, "upgrade", REVISION)
        assert invalid_name not in str(error.value)
    finally:
        engine.dispose()


def test_user_domain_lifecycle_migrates_legacy_rows(migration_database_url):
    run_alembic(migration_database_url, "upgrade", BASE_REVISION)
    engine = create_engine(migration_database_url)
    user_id = uuid.uuid4()
    domain_id = uuid.uuid4()
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO users (id, username, password_hash, role, is_active, created_at, updated_at)
                    VALUES (:id, '  LegacyUser  ', 'hash', 'operator', true, now(), now())
                    """
                ),
                {"id": user_id},
            )
            connection.execute(
                text(
                    """
                    INSERT INTO domains (id, name, is_active, is_default, created_at)
                    VALUES (:id, '  Go.Example.COM  ', true, false, now())
                    """
                ),
                {"id": domain_id},
            )
            connection.execute(
                text(
                    """
                    INSERT INTO user_domains (id, user_id, domain_id, created_at)
                    VALUES (:id, :user_id, :domain_id, now())
                    """
                ),
                {"id": uuid.uuid4(), "user_id": user_id, "domain_id": domain_id},
            )

        run_alembic(migration_database_url, "upgrade", REVISION)

        with engine.connect() as connection:
            assert connection.scalar(text("SELECT username FROM users WHERE id = :id"), {"id": user_id}) == "legacyuser"
            assert connection.scalar(text("SELECT name FROM domains WHERE id = :id"), {"id": domain_id}) == "go.example.com"
            assert connection.scalar(text("SELECT timezone FROM domains WHERE id = :id"), {"id": domain_id}) == "Asia/Shanghai"
            assert connection.scalar(text("SELECT updated_at IS NOT NULL FROM domains WHERE id = :id"), {"id": domain_id}) is True
            assert connection.scalar(text("SELECT granted_by FROM user_domains WHERE user_id = :id"), {"id": user_id}) is None

        inspector = inspect(engine)
        check_names = {check["name"] for check in inspector.get_check_constraints("users")}
        assert {"ck_users_role", "ck_users_username_lower"} <= check_names
        domain_check_names = {check["name"] for check in inspector.get_check_constraints("domains")}
        assert "ck_domains_name_lower_host" in domain_check_names
        index_names = {index["name"] for index in inspector.get_indexes("user_domains")}
        assert "idx_user_domains_domain" in index_names
        domain_indexes = {index["name"]: index for index in inspector.get_indexes("domains")}
        assert domain_indexes["uq_domains_one_default"]["unique"] is True
        assert domain_indexes["uq_domains_one_default"]["dialect_options"]["postgresql_where"] is not None
        foreign_keys = {
            foreign_key["constrained_columns"][0]: foreign_key
            for foreign_key in inspector.get_foreign_keys("user_domains")
        }
        assert foreign_keys["user_id"]["options"]["ondelete"] == "RESTRICT"
        assert foreign_keys["domain_id"]["options"]["ondelete"] == "RESTRICT"
        assert foreign_keys["granted_by"]["options"]["ondelete"] == "RESTRICT"
        domain_columns = {
            column["name"]: column for column in inspector.get_columns("domains")
        }
        assert "now()" in domain_columns["updated_at"]["default"]
        assert "Asia/Shanghai" in domain_columns["timezone"]["default"]

        run_alembic(migration_database_url, "downgrade", BASE_REVISION)
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT username FROM users WHERE id = :id"), {"id": user_id}) == "legacyuser"
        downgraded_columns = {column["name"] for column in inspect(engine).get_columns("domains")}
        assert "timezone" not in downgraded_columns
        assert "updated_at" not in downgraded_columns
        assert "granted_by" not in {
            column["name"] for column in inspect(engine).get_columns("user_domains")
        }
    finally:
        engine.dispose()
