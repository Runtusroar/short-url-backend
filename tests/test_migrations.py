import uuid
from dataclasses import dataclass

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

from app.core.config import settings
from scripts.backfill_user_agents import backfill_user_agents


@dataclass(frozen=True)
class LegacyCounts:
    users: int
    domains: int
    short_links: int
    target_urls: int
    access_logs: int


def _alembic_config() -> Config:
    return Config("alembic.ini")


def _counts(connection) -> LegacyCounts:
    return LegacyCounts(
        **{
            table: connection.execute(text(f"SELECT count(*) FROM {table}")).scalar_one()
            for table in LegacyCounts.__annotations__
        }
    )


def test_upgrade_preserves_legacy_rows_and_backfills_log_snapshots(monkeypatch):
    """Dropping or changing a legacy value during normalization is data loss."""
    schema = f"migration_{uuid.uuid4().hex}"
    base_url = settings.database_url.replace("+asyncpg", "+psycopg")
    schema_url = f"{base_url}?options=-csearch_path%3D{schema}"
    engine = create_engine(schema_url)
    monkeypatch.setattr(settings, "database_url", schema_url)

    try:
        with engine.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        command.upgrade(_alembic_config(), "a9e56b03bf5f")

        user_id, client_id, domain_id, link_id, target_id, log_id, grant_id, rule_id = (
            uuid.uuid4() for _ in range(8)
        )
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO users (id, username, password_hash, role, is_active, created_at, updated_at)
                    VALUES (:id, 'legacy-admin', 'hash', 'admin', true, now(), now())
                    """
                ),
                {"id": user_id},
            )
            connection.execute(
                text(
                    """
                    INSERT INTO users (id, username, password_hash, role, is_active, created_at, updated_at)
                    VALUES (:id, 'legacy-client', 'hash', 'client', true, now(), now())
                    """
                ),
                {"id": client_id},
            )
            connection.execute(
                text(
                    """
                    INSERT INTO domains (id, name, is_active, is_default, created_at)
                    VALUES (:id, 'legacy.example', true, true, now())
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
                {"id": grant_id, "user_id": client_id, "domain_id": domain_id},
            )
            connection.execute(
                text(
                    """
                    INSERT INTO short_links
                        (id, domain_id, short_code, is_custom_alias, description, owner_id, is_active, created_at, updated_at, default_action)
                    VALUES (:id, :domain_id, 'legacy-code', false, 'legacy note', :owner_id, true, now(), now(), 'deny')
                    """
                ),
                {"id": link_id, "domain_id": domain_id, "owner_id": user_id},
            )
            connection.execute(
                text(
                    """
                    INSERT INTO access_rules
                        (id, short_link_id, action, priority, countries, ua_platforms, referer_pattern,
                         allow_proxy, allow_bot, is_active)
                    VALUES (:id, :link_id, 'allow', 1, CAST(:countries AS jsonb), '[]'::jsonb, NULL,
                            true, true, true)
                    """
                ),
                {"id": rule_id, "link_id": link_id, "countries": '["cn"]'},
            )
            connection.execute(
                text(
                    """
                    INSERT INTO target_urls (id, short_link_id, url, url_type, weight, is_active, created_at)
                    VALUES (:id, :link_id, 'https://example.test/blocked', 'denied', 1, true, now())
                    """
                ),
                {"id": target_id, "link_id": link_id},
            )
            connection.execute(
                text(
                    """
                    INSERT INTO access_logs
                        (id, short_link_id, domain_id, target_url_id, result, ip, country, ua_string, ua_platform, referer,
                         accessed_at, accessed_at_plus8, dedup_bucket)
                    VALUES (:id, :link_id, :domain_id, :target_id, 'denied', 'not-an-ip', 'CN', 'legacy ua', 'mobile',
                            'https://referrer.test', now(), CURRENT_DATE, 1)
                    """
                ),
                {"id": log_id, "link_id": link_id, "domain_id": domain_id, "target_id": target_id},
            )
            before = _counts(connection)

        command.upgrade(_alembic_config(), "20260912_refactor")

        with engine.connect() as connection:
            assert _counts(connection) == before
            migrated_log = connection.execute(
                text(
                    """
                    SELECT block_reason, short_code, ip::text AS ip
                    FROM access_logs WHERE id = :id
                    """
                ),
                {"id": log_id},
            ).mappings().one()
            assert migrated_log["block_reason"] == "other"
            assert migrated_log["short_code"] == "legacy-code"
            assert migrated_log["ip"] is None
            assert connection.execute(
                text("SELECT note FROM short_links WHERE id = :id"), {"id": link_id}
            ).scalar_one() == "legacy note"
            assert connection.execute(
                text("SELECT url_type FROM target_urls WHERE id = :id"), {"id": target_id}
            ).scalar_one() == "blocked"
            assert connection.execute(
                text("SELECT access_level FROM user_domain_access WHERE user_id = :id"),
                {"id": client_id},
            ).scalar_one() == "read"
            policy = connection.execute(
                text("SELECT country_mode, countries FROM link_policies WHERE short_link_id = :id"),
                {"id": link_id},
            ).mappings().one()
            assert policy["country_mode"] == "allow"
            assert policy["countries"] == ["CN"]
    finally:
        with engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        engine.dispose()


def test_upgrade_refuses_unrepresentable_legacy_rule_before_dropping_it(monkeypatch):
    """A destructive migration must stop when an unconditional deny cannot be encoded."""
    schema = f"policy_gate_{uuid.uuid4().hex}"
    base_url = settings.database_url.replace("+asyncpg", "+psycopg")
    schema_url = f"{base_url}?options=-csearch_path%3D{schema}"
    engine = create_engine(schema_url)
    monkeypatch.setattr(settings, "database_url", schema_url)

    try:
        with engine.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        command.upgrade(_alembic_config(), "a9e56b03bf5f")
        user_id, domain_id, link_id, rule_id = (uuid.uuid4() for _ in range(4))
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO users (id, username, password_hash, role, is_active, created_at, updated_at)
                    VALUES (:id, 'gate-admin', 'hash', 'admin', true, now(), now())
                    """
                ),
                {"id": user_id},
            )
            connection.execute(
                text(
                    """
                    INSERT INTO domains (id, name, is_active, is_default, created_at)
                    VALUES (:id, 'gate.example', true, true, now())
                    """
                ),
                {"id": domain_id},
            )
            connection.execute(
                text(
                    """
                    INSERT INTO short_links
                        (id, domain_id, short_code, is_custom_alias, owner_id, is_active, created_at, updated_at, default_action)
                    VALUES (:id, :domain_id, 'gate-code', false, :owner_id, true, now(), now(), 'allow')
                    """
                ),
                {"id": link_id, "domain_id": domain_id, "owner_id": user_id},
            )
            connection.execute(
                text(
                    """
                    INSERT INTO access_rules
                        (id, short_link_id, action, priority, countries, ua_platforms, referer_pattern,
                         allow_proxy, allow_bot, is_active)
                    VALUES (:id, :link_id, 'deny', 1, '[]'::jsonb, '[]'::jsonb, NULL, true, true, true)
                    """
                ),
                {"id": rule_id, "link_id": link_id},
            )

        with pytest.raises(RuntimeError, match="unconditional deny"):
            command.upgrade(_alembic_config(), "20260912_refactor")
        with engine.connect() as connection:
            assert connection.execute(text("SELECT count(*) FROM access_rules")).scalar_one() == 1
    finally:
        with engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        engine.dispose()


def test_upgrade_refuses_invalid_blacklist_ip_without_dropping_the_row(monkeypatch):
    """A non-null exact-IP blacklist cannot silently discard an invalid legacy value."""
    schema = f"blacklist_gate_{uuid.uuid4().hex}"
    base_url = settings.database_url.replace("+asyncpg", "+psycopg")
    schema_url = f"{base_url}?options=-csearch_path%3D{schema}"
    engine = create_engine(schema_url)
    monkeypatch.setattr(settings, "database_url", schema_url)
    blacklist_id = uuid.uuid4()

    try:
        with engine.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        command.upgrade(_alembic_config(), "a9e56b03bf5f")
        with engine.begin() as connection:
            connection.execute(
                text("INSERT INTO ip_blacklist (id, ip, created_at) VALUES (:id, 'not-an-ip', now())"),
                {"id": blacklist_id},
            )

        with pytest.raises(RuntimeError, match="invalid IP blacklist"):
            command.upgrade(_alembic_config(), "20260912_refactor")
        with engine.connect() as connection:
            assert connection.execute(
                text("SELECT ip FROM ip_blacklist WHERE id = :id"), {"id": blacklist_id}
            ).scalar_one() == "not-an-ip"
    finally:
        with engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        engine.dispose()


def test_fresh_upgrade_creates_normalized_schema(monkeypatch):
    """A new deployment must reach the same normalized schema without legacy tables."""
    schema = f"fresh_{uuid.uuid4().hex}"
    base_url = settings.database_url.replace("+asyncpg", "+psycopg")
    schema_url = f"{base_url}?options=-csearch_path%3D{schema}"
    engine = create_engine(schema_url)
    monkeypatch.setattr(settings, "database_url", schema_url)

    try:
        with engine.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        command.upgrade(_alembic_config(), "20260912_refactor")
        with engine.connect() as connection:
            tables = set(
                connection.execute(
                    text("SELECT tablename FROM pg_tables WHERE schemaname = :schema"), {"schema": schema}
                ).scalars()
            )
            assert {"user_domain_access", "link_policies", "ip_reputation"} <= tables
            assert {"user_domains", "access_rules", "short_link_permissions"}.isdisjoint(tables)
    finally:
        with engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        engine.dispose()


@pytest.mark.asyncio
async def test_user_agent_backfill_marks_processed_rows_and_is_idempotent():
    """Re-running the maintenance command must not keep rewriting processed rows."""
    engine = create_engine(settings.database_url.replace("+asyncpg", "+psycopg"))
    log_id = uuid.uuid4()
    try:
        # Other API tests may have produced unprocessed audit rows. Drain that
        # shared queue before asserting this fixture's one-row batch behavior.
        await backfill_user_agents(1000)
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO access_logs (id, result, ua_raw, accessed_at)
                    VALUES (:id, 'allowed', 'Mozilla/5.0 Chrome/120.0.0.0 Safari/537.36', now())
                    """
                ),
                {"id": log_id},
            )
        assert await backfill_user_agents(1) == 1
        with engine.connect() as connection:
            assert connection.execute(
                text("SELECT ua_browser FROM access_logs WHERE id = :id"), {"id": log_id}
            ).scalar_one() is not None
        assert await backfill_user_agents(1) == 0
    finally:
        with engine.begin() as connection:
            connection.execute(text("DELETE FROM access_logs WHERE id = :id"), {"id": log_id})
        engine.dispose()
