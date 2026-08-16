"""Database contract for append-only, explainable access-log history."""

from datetime import datetime
from subprocess import CalledProcessError
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, inspect, text

from tests.migrations.support import run_alembic

BASE_REVISION = "e52d9a6c8031"
REVISION = "a73f0b9d4216"


def _seed_user(connection, suffix: str) -> UUID:
    user_id = uuid4()
    connection.execute(
        text(
            """
            INSERT INTO users (id, username, password_hash, role, is_active, created_at, updated_at)
            VALUES (:id, :username, 'hash', 'admin', true, now(), now())
            """
        ),
        {"id": user_id, "username": f"access-log-{suffix}-{user_id.hex[:8]}"},
    )
    return user_id


def _seed_domain_link_and_target(connection, *, timezone: str, suffix: str, user_id: UUID):
    domain_id = uuid4()
    link_id = uuid4()
    target_id = uuid4()
    connection.execute(
        text(
            """
            INSERT INTO domains (id, name, timezone, is_active, is_default, created_at, updated_at)
            VALUES (:id, :name, :timezone, true, false, now(), now())
            """
        ),
        {"id": domain_id, "name": f"{suffix}.example.test", "timezone": timezone},
    )
    connection.execute(
        text(
            """
            INSERT INTO short_links (
                id, domain_id, short_code, is_custom_alias, name, owner_id,
                is_active, default_action, created_at, updated_at
            )
            VALUES (:id, :domain_id, :short_code, false, :name, :owner_id,
                    true, 'allow', now(), now())
            """
        ),
        {
            "id": link_id,
            "domain_id": domain_id,
            "short_code": f"log-{suffix}",
            "name": f"Legacy {suffix}",
            "owner_id": user_id,
        },
    )
    connection.execute(
        text(
            """
            INSERT INTO target_urls (
                id, short_link_id, name, url, url_type, weight, is_active, created_at, updated_at
            )
            VALUES (:id, :short_link_id, :name, :url, 'allowed', 1, true, now(), now())
            """
        ),
        {
            "id": target_id,
            "short_link_id": link_id,
            "name": f"Target {suffix}",
            "url": f"https://{suffix}.target.example/path",
        },
    )
    return domain_id, link_id, target_id


def test_access_log_migration_preserves_history_and_domain_business_dates(
    migration_database_url,
):
    run_alembic(migration_database_url, "upgrade", BASE_REVISION)
    engine = create_engine(migration_database_url)
    shanghai_log_id = uuid4()
    new_york_log_id = uuid4()
    blocked_log_id = uuid4()
    try:
        with engine.begin() as connection:
            user_id = _seed_user(connection, "contract")
            shanghai_domain_id, shanghai_link_id, shanghai_target_id = _seed_domain_link_and_target(
                connection,
                timezone="Asia/Shanghai",
                suffix="shanghai",
                user_id=user_id,
            )
            new_york_domain_id, new_york_link_id, new_york_target_id = _seed_domain_link_and_target(
                connection,
                timezone="America/New_York",
                suffix="new-york",
                user_id=user_id,
            )
            connection.execute(
                text(
                    """
                    INSERT INTO access_logs (
                        id, short_link_id, domain_id, target_url_id, result, ip, country,
                        ua_string, ua_platform, referer, accessed_at, accessed_at_plus8, dedup_bucket
                    )
                    VALUES
                    (:shanghai_id, :shanghai_link_id, :shanghai_domain_id, :shanghai_target_id,
                     'allowed', '2001:0db8:0:0:0:0:0:9', 'CN', 'Mozilla', 'pc',
                     'https://ref.example', :timestamp, :plus8_date, 100),
                    (:new_york_id, :new_york_link_id, :new_york_domain_id, :new_york_target_id,
                     'allowed', '192.0.2.8', 'US', 'Googlebot', 'bot', NULL,
                     :timestamp, :plus8_date, 100),
                    (:blocked_id, :shanghai_link_id, :shanghai_domain_id, NULL,
                     'blocked', '192.0.2.9', NULL, NULL, NULL, NULL,
                     :timestamp, :plus8_date, 100)
                    """
                ),
                {
                    "shanghai_id": shanghai_log_id,
                    "new_york_id": new_york_log_id,
                    "blocked_id": blocked_log_id,
                    "shanghai_link_id": shanghai_link_id,
                    "shanghai_domain_id": shanghai_domain_id,
                    "shanghai_target_id": shanghai_target_id,
                    "new_york_link_id": new_york_link_id,
                    "new_york_domain_id": new_york_domain_id,
                    "new_york_target_id": new_york_target_id,
                    "timestamp": datetime.fromisoformat("2026-01-01T02:30:00+00:00"),
                    "plus8_date": datetime.fromisoformat("2026-01-01T10:30:00+08:00").date(),
                },
            )

        run_alembic(migration_database_url, "upgrade", REVISION)

        with engine.connect() as connection:
            rows = connection.execute(
                text(
                    """
                    SELECT id, host(client_ip) AS client_ip, user_agent, access_date,
                           target_url_snapshot, request_host, request_method,
                           decision_reason, proxy_check_status, is_anonymous,
                           proxy_types, proxy_source, matched_rule_id
                    FROM access_logs
                    ORDER BY id
                    """
                )
            ).mappings().all()
            by_id = {row["id"]: row for row in rows}
            assert len(rows) == 3
            assert by_id[shanghai_log_id]["client_ip"] == "2001:db8::9"
            assert by_id[shanghai_log_id]["user_agent"] == "Mozilla"
            assert by_id[shanghai_log_id]["access_date"].isoformat() == "2026-01-01"
            assert by_id[new_york_log_id]["access_date"].isoformat() == "2025-12-31"
            assert by_id[shanghai_log_id]["target_url_snapshot"] == "https://shanghai.target.example/path"
            assert by_id[shanghai_log_id]["request_host"] == "shanghai.example.test"
            assert by_id[shanghai_log_id]["request_method"] == "GET"
            assert by_id[shanghai_log_id]["decision_reason"] == "legacy_unknown"
            assert by_id[shanghai_log_id]["proxy_check_status"] == "skipped"
            assert by_id[shanghai_log_id]["is_anonymous"] is None
            assert by_id[shanghai_log_id]["proxy_types"] == []
            assert by_id[shanghai_log_id]["proxy_source"] is None
            assert by_id[shanghai_log_id]["matched_rule_id"] is None
            assert by_id[new_york_log_id]["proxy_check_status"] == "assumed_bot"
            assert by_id[new_york_log_id]["is_anonymous"] is True
            assert by_id[new_york_log_id]["proxy_source"] == "assumed_bot"
            assert by_id[blocked_log_id]["decision_reason"] == "blacklist"

        inspector = inspect(engine)
        columns = {column["name"]: column for column in inspector.get_columns("access_logs")}
        assert columns["client_ip"]["type"].__class__.__name__ == "INET"
        assert {"ip", "ua_string", "accessed_at_plus8"}.isdisjoint(columns)
        foreign_keys = {
            foreign_key["constrained_columns"][0]: foreign_key
            for foreign_key in inspector.get_foreign_keys("access_logs")
        }
        assert foreign_keys["short_link_id"]["options"]["ondelete"] == "RESTRICT"
        assert foreign_keys["domain_id"]["options"]["ondelete"] == "RESTRICT"
        assert foreign_keys["target_url_id"]["options"]["ondelete"] == "SET NULL"
        assert foreign_keys["matched_rule_id"]["options"]["ondelete"] == "SET NULL"
        indexes = {index["name"]: index for index in inspector.get_indexes("access_logs")}
        assert set(indexes) >= {
            "idx_access_logs_domain_accessed_at",
            "idx_access_logs_link_accessed_at",
            "idx_access_logs_link_access_date",
            "idx_access_logs_link_client_ip_dedup",
            "idx_access_logs_domain_result_accessed_at",
            "idx_access_logs_domain_country_accessed_at",
        }
    finally:
        engine.dispose()


def test_access_log_upgrade_rejects_invalid_legacy_ip_before_writing(
    migration_database_url,
):
    run_alembic(migration_database_url, "upgrade", BASE_REVISION)
    engine = create_engine(migration_database_url)
    secret_ip = "not-a-real-client-address"
    try:
        with engine.begin() as connection:
            user_id = _seed_user(connection, "invalid-ip")
            domain_id, link_id, _ = _seed_domain_link_and_target(
                connection,
                timezone="Asia/Shanghai",
                suffix="invalid-ip",
                user_id=user_id,
            )
            connection.execute(
                text(
                    """
                    INSERT INTO access_logs (
                        id, short_link_id, domain_id, result, ip, accessed_at,
                        accessed_at_plus8, dedup_bucket
                    )
                    VALUES (:id, :link_id, :domain_id, 'allowed', :ip, now(), current_date, 1)
                    """
                ),
                {"id": uuid4(), "link_id": link_id, "domain_id": domain_id, "ip": secret_ip},
            )

        with pytest.raises(CalledProcessError) as exc_info:
            run_alembic(migration_database_url, "upgrade", REVISION)
        output = f"{exc_info.value.stdout}\n{exc_info.value.stderr}"
        assert "access log IP invariant" in output
        assert secret_ip not in output

        with engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == BASE_REVISION
            assert connection.scalar(
                text(
                    "SELECT count(*) FROM information_schema.columns "
                    "WHERE table_name = 'access_logs' AND column_name = 'client_ip'"
                )
            ) == 0
    finally:
        engine.dispose()


def test_access_log_representable_round_trip_restores_legacy_columns_and_defaults(
    migration_database_url,
):
    run_alembic(migration_database_url, "upgrade", BASE_REVISION)
    engine = create_engine(migration_database_url)
    try:
        with engine.begin() as connection:
            user_id = _seed_user(connection, "round-trip")
            domain_id, link_id, _ = _seed_domain_link_and_target(
                connection,
                timezone="Asia/Shanghai",
                suffix="round-trip",
                user_id=user_id,
            )
            connection.execute(
                text(
                    """
                    INSERT INTO access_logs (
                        short_link_id, domain_id, result, ip, accessed_at,
                        accessed_at_plus8, dedup_bucket
                    )
                    VALUES (:link_id, :domain_id, 'allowed', '198.51.100.9', now(), current_date, 1)
                    """
                ),
                {"link_id": link_id, "domain_id": domain_id},
            )
        run_alembic(migration_database_url, "upgrade", REVISION)

        with engine.begin() as connection:
            inserted = connection.execute(
                text(
                    """
                    INSERT INTO access_logs (
                        short_link_id, domain_id, result, client_ip, access_date,
                        dedup_bucket, decision_reason, request_method, proxy_check_status
                    )
                    VALUES (:link_id, :domain_id, 'allowed', '203.0.113.18', current_date,
                            2, 'legacy_unknown', 'GET', 'skipped')
                    RETURNING id, accessed_at, proxy_types
                    """
                ),
                {"link_id": link_id, "domain_id": domain_id},
            ).mappings().one()
            assert inserted["id"] is not None
            assert inserted["accessed_at"] is not None
            assert inserted["proxy_types"] == []

        run_alembic(migration_database_url, "downgrade", BASE_REVISION)
        with engine.connect() as connection:
            restored = connection.execute(
                text("SELECT ip, ua_string, accessed_at_plus8 FROM access_logs ORDER BY dedup_bucket")
            ).mappings().all()
            assert [row["ip"] for row in restored] == ["198.51.100.9", "203.0.113.18"]
            assert all(row["accessed_at_plus8"] is not None for row in restored)
            columns = {column["name"]: column for column in inspect(engine).get_columns("access_logs")}
            assert {"ip", "ua_string", "accessed_at_plus8"} <= columns.keys()
            assert {"client_ip", "decision_reason", "proxy_types"}.isdisjoint(columns)
            indexes = {index["name"] for index in inspect(engine).get_indexes("access_logs")}
            assert {
                "idx_access_logs_domain",
                "idx_access_logs_short_link",
                "idx_access_logs_plus8",
                "idx_access_logs_dedup",
            } <= indexes
    finally:
        engine.dispose()


def test_access_log_downgrade_rejects_unknown_client_ip_before_writing(
    migration_database_url,
):
    run_alembic(migration_database_url, "upgrade", BASE_REVISION)
    engine = create_engine(migration_database_url)
    try:
        with engine.begin() as connection:
            user_id = _seed_user(connection, "downgrade-null")
            domain_id, link_id, _ = _seed_domain_link_and_target(
                connection,
                timezone="Asia/Shanghai",
                suffix="downgrade-null",
                user_id=user_id,
            )
            connection.execute(
                text(
                    """
                    INSERT INTO access_logs (
                        short_link_id, domain_id, result, ip, accessed_at,
                        accessed_at_plus8, dedup_bucket
                    )
                    VALUES (:link_id, :domain_id, 'allowed', '192.0.2.20', now(), current_date, 1)
                    """
                ),
                {"link_id": link_id, "domain_id": domain_id},
            )
        run_alembic(migration_database_url, "upgrade", REVISION)
        with engine.begin() as connection:
            connection.execute(text("UPDATE access_logs SET client_ip = NULL"))

        with pytest.raises(CalledProcessError) as exc_info:
            run_alembic(migration_database_url, "downgrade", BASE_REVISION)
        output = f"{exc_info.value.stdout}\n{exc_info.value.stderr}"
        assert "access log downgrade client IP invariant" in output
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == REVISION
            assert connection.scalar(
                text("SELECT count(*) FROM information_schema.columns WHERE table_name = 'access_logs' AND column_name = 'client_ip'")
            ) == 1
            assert connection.scalar(text("SELECT count(*) FROM access_logs WHERE client_ip IS NULL")) == 1
    finally:
        engine.dispose()
