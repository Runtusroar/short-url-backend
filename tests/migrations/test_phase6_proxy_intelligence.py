"""Phase 6 proxy error persistence migration contracts."""

from subprocess import CalledProcessError

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from tests.migrations.support import get_schema_contract, run_alembic

HEAD = "f84c2d7a901e"
PREVIOUS = "c8e4f1a26b73"
PROXY_ERROR_CODES = {
    "disabled",
    "redis_unavailable",
    "auth_failed",
    "insufficient_funds",
    "permission_denied",
    "rate_limited",
    "timeout",
    "upstream_error",
    "invalid_response",
    "ip_not_found",
    "invalid_ip",
    "non_global_ip",
    "lookup_contended",
}


def _scalar(database_url: str, statement: str) -> object:
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            return connection.scalar(text(statement))
    finally:
        engine.dispose()


def _execute(database_url: str, statement: str) -> None:
    engine = create_engine(database_url)
    try:
        with engine.begin() as connection:
            connection.execute(text(statement))
    finally:
        engine.dispose()


def _seed_access_log(database_url: str) -> None:
    _execute(
        database_url,
        """
        WITH new_user AS (
            INSERT INTO users
                (username, password_hash, role, is_active, created_at, updated_at)
            VALUES ('phase6-owner', 'hash', 'admin', true, now(), now())
            RETURNING id
        ), new_domain AS (
            INSERT INTO domains
                (name, timezone, is_active, is_default, created_at, updated_at)
            VALUES ('phase6.example.test', 'UTC', true, false, now(), now())
            RETURNING id
        ), new_link AS (
            INSERT INTO short_links
                (domain_id, short_code, is_custom_alias, name, owner_id,
                 is_active, default_action, created_at, updated_at)
            SELECT new_domain.id, 'phase6', false, 'Phase 6', new_user.id,
                   true, 'allow', now(), now()
            FROM new_user, new_domain
            RETURNING id, domain_id
        )
        INSERT INTO access_logs
            (short_link_id, domain_id, result, access_date, dedup_bucket,
             decision_reason, request_method, proxy_check_status)
        SELECT id, domain_id, 'allowed', current_date, 1,
               'default_action', 'GET', 'skipped'
        FROM new_link
        """,
    )


def test_phase6_upgrade_and_representable_downgrade(migration_database_url):
    run_alembic(migration_database_url, "upgrade", PREVIOUS)
    _seed_access_log(migration_database_url)

    run_alembic(migration_database_url, "upgrade", HEAD)

    contract = get_schema_contract(migration_database_url)
    assert contract["columns"]["access_logs"]["proxy_error_code"] == {
        "type": "VARCHAR(32)",
        "nullable": True,
        "default": None,
        "timezone": None,
    }
    assert "ck_access_logs_proxy_error_code" in contract["checks"]["access_logs"]
    assert _scalar(migration_database_url, "SELECT proxy_error_code FROM access_logs") is None

    run_alembic(migration_database_url, "downgrade", PREVIOUS)
    assert "proxy_error_code" not in get_schema_contract(migration_database_url)["columns"][
        "access_logs"
    ]


def test_phase6_downgrade_rejects_persisted_error_before_writes(
    migration_database_url,
):
    run_alembic(migration_database_url, "upgrade", HEAD)
    _seed_access_log(migration_database_url)
    _execute(migration_database_url, "UPDATE access_logs SET proxy_error_code = 'timeout'")

    with pytest.raises(CalledProcessError) as exc_info:
        run_alembic(migration_database_url, "downgrade", PREVIOUS)

    assert "proxy error downgrade invariant" in (
        exc_info.value.stdout + exc_info.value.stderr
    )
    assert _scalar(migration_database_url, "SELECT version_num FROM alembic_version") == HEAD
    assert "proxy_error_code" in get_schema_contract(migration_database_url)["columns"][
        "access_logs"
    ]


@pytest.mark.parametrize("value", sorted(PROXY_ERROR_CODES))
def test_proxy_error_constraint_permits_only_the_contract_values(
    migration_database_url, value
):
    run_alembic(migration_database_url, "upgrade", HEAD)
    _seed_access_log(migration_database_url)

    _execute(
        migration_database_url,
        f"UPDATE access_logs SET proxy_error_code = '{value}'",
    )
    assert _scalar(migration_database_url, "SELECT proxy_error_code FROM access_logs") == value

    with pytest.raises(IntegrityError):
        _execute(
            migration_database_url,
            "UPDATE access_logs SET proxy_error_code = 'unknown_proxy_error'",
        )
