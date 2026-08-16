"""End-to-end Phase 4 migration contract on disposable PostgreSQL databases."""

from subprocess import CalledProcessError
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, text

from app.core.database import Base
from tests.migrations.support import get_schema_contract, run_alembic

HEAD = "a73f0b9d4216"
D6 = "d6e8f0a21b35"
TASK_PREVIOUS_REVISIONS = (
    D6,
    "f31a8c0d4e72",
    "9b6d2f4a7c11",
    "de4c81b75920",
    "e52d9a6c8031",
)
SEEDED_ROWS = {
    "users": "user",
    "domains": "domain",
    "user_domains": "grant",
    "short_links": "link",
    "short_link_permissions": "permission",
    "target_urls": "target",
    "access_rules": "rule",
    "ip_blacklist": "blacklist",
    "access_logs": "log",
}


def _normalized_type_name(value: object) -> str:
    """Compare SQLAlchemy's generic DateTime spelling with PostgreSQL's type."""
    return "TIMESTAMP" if str(value).upper() == "DATETIME" else str(value).upper()


def _table_counts(connection) -> dict[str, int]:
    return {
        table_name: connection.scalar(text(f"SELECT count(*) FROM {table_name}"))
        for table_name in SEEDED_ROWS
    }


def _seed_representable_d6_rows(connection) -> tuple[dict[str, UUID], dict[str, int]]:
    ids = {
        name: uuid4()
        for name in (
            "user",
            "domain",
            "grant",
            "link",
            "permission",
            "target",
            "rule",
            "blacklist",
            "log",
        )
    }
    connection.execute(
        text(
            "INSERT INTO users (id, username, password_hash, role, is_active, created_at, updated_at) VALUES (:id, 'phase4-legacy', 'hash', 'admin', true, now(), now())"
        ),
        {"id": ids["user"]},
    )
    connection.execute(
        text(
            "INSERT INTO domains (id, name, is_active, is_default, created_at) VALUES (:id, 'phase4.example.test', true, false, now())"
        ),
        {"id": ids["domain"]},
    )
    connection.execute(
        text(
            "INSERT INTO user_domains (id, user_id, domain_id, created_at) VALUES (:id, :user_id, :domain_id, now())"
        ),
        {"id": ids["grant"], "user_id": ids["user"], "domain_id": ids["domain"]},
    )
    connection.execute(
        text(
            "INSERT INTO short_links (id, domain_id, short_code, is_custom_alias, description, owner_id, is_active, default_action, created_at, updated_at) VALUES (:id, :domain_id, 'phase4', false, 'Phase 4 legacy', :owner_id, true, 'allow', now(), now())"
        ),
        {"id": ids["link"], "domain_id": ids["domain"], "owner_id": ids["user"]},
    )
    connection.execute(
        text(
            "INSERT INTO short_link_permissions (id, short_link_id, user_id, created_at) VALUES (:id, :link_id, :user_id, now())"
        ),
        {"id": ids["permission"], "link_id": ids["link"], "user_id": ids["user"]},
    )
    connection.execute(
        text(
            "INSERT INTO target_urls (id, short_link_id, url, url_type, weight, is_active, created_at) VALUES (:id, :link_id, 'https://target.example.test', 'allowed', 1, true, now())"
        ),
        {"id": ids["target"], "link_id": ids["link"]},
    )
    connection.execute(
        text(
            "INSERT INTO access_rules (id, short_link_id, action, priority, countries, ua_platforms, referer_pattern, allow_bot, allow_proxy, is_active) VALUES (:id, :link_id, 'allow', 0, '[]'::jsonb, '[]'::jsonb, NULL, true, true, true)"
        ),
        {"id": ids["rule"], "link_id": ids["link"]},
    )
    connection.execute(
        text(
            "INSERT INTO ip_blacklist (id, ip, reason, created_by, created_at) VALUES (:id, '198.51.100.8', 'legacy', :user_id, now())"
        ),
        {"id": ids["blacklist"], "user_id": ids["user"]},
    )
    connection.execute(
        text(
            "INSERT INTO access_logs (id, short_link_id, domain_id, target_url_id, result, ip, country, ua_string, ua_platform, referer, accessed_at, accessed_at_plus8, dedup_bucket) VALUES (:id, :link_id, :domain_id, :target_id, 'allowed', '198.51.100.9', 'US', 'legacy agent', 'pc', NULL, now(), current_date, 1)"
        ),
        {
            "id": ids["log"],
            "link_id": ids["link"],
            "domain_id": ids["domain"],
            "target_id": ids["target"],
        },
    )
    return ids, _table_counts(connection)


def _assert_seed_ids_counts_and_conversions(
    database_url: str, ids: dict[str, UUID], expected_counts: dict[str, int]
) -> None:
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            assert _table_counts(connection) == expected_counts
            for table_name, row_key in SEEDED_ROWS.items():
                assert (
                    connection.scalar(
                        text(f"SELECT count(*) FROM {table_name} WHERE id = :id"),
                        {"id": ids[row_key]},
                    )
                    == 1
                )
            assert (
                connection.scalar(
                    text("SELECT name FROM short_links WHERE id = :id"),
                    {"id": ids["link"]},
                )
                == "Phase 4 legacy"
            )
            assert connection.execute(
                text(
                    "SELECT name, client_requirement, proxy_requirement "
                    "FROM access_rules WHERE id = :id"
                ),
                {"id": ids["rule"]},
            ).one() == ("Rule 0", "any", "any")
            assert connection.execute(
                text(
                    "SELECT host(client_ip), user_agent, request_host, request_method, "
                    "decision_reason, proxy_check_status, proxy_types "
                    "FROM access_logs WHERE id = :id"
                ),
                {"id": ids["log"]},
            ).one() == (
                "198.51.100.9",
                "legacy agent",
                "phase4.example.test",
                "GET",
                "legacy_unknown",
                "skipped",
                [],
            )
    finally:
        engine.dispose()


def test_empty_database_reaches_the_final_schema_contract(migration_database_url):
    run_alembic(migration_database_url, "upgrade", "head")

    contract = get_schema_contract(migration_database_url)

    assert contract["tables"] == {
        "access_logs",
        "access_rules",
        "alembic_version",
        "domains",
        "ip_blacklist",
        "short_link_permissions",
        "short_links",
        "target_urls",
        "user_domains",
        "users",
    }
    assert contract["indexes"]["domains"]["uq_domains_one_default"]["unique"] is True
    assert (
        contract["indexes"]["domains"]["uq_domains_one_default"]["predicate"]
        == "is_default"
    )
    assert contract["indexes"]["short_links"]["idx_short_links_domain_created_at"][
        "sorting"
    ] == {"created_at": ("desc",)}

    engine = create_engine(migration_database_url)
    try:
        with engine.connect() as connection:
            assert (
                connection.exec_driver_sql(
                    "SELECT version_num FROM alembic_version"
                ).scalar_one()
                == HEAD
            )
    finally:
        engine.dispose()


def test_phase_three_representative_rows_upgrade_to_head_with_ids_and_counts(
    migration_database_url,
):
    run_alembic(migration_database_url, "upgrade", D6)
    engine = create_engine(migration_database_url)
    try:
        with engine.begin() as connection:
            ids, expected_counts = _seed_representable_d6_rows(connection)
    finally:
        engine.dispose()
    run_alembic(migration_database_url, "upgrade", "head")
    _assert_seed_ids_counts_and_conversions(
        migration_database_url, ids, expected_counts
    )


@pytest.mark.parametrize(
    ("statement", "parameters", "invariant"),
    [
        (
            "INSERT INTO users (id, username, password_hash, role, is_active, created_at, updated_at) VALUES (:first, 'Alice', 'hash', 'admin', true, now(), now()), (:second, 'alice', 'hash', 'admin', true, now(), now())",
            lambda: {"first": uuid4(), "second": uuid4()},
            "username normalization invariant",
        ),
        (
            "INSERT INTO domains (id, name, is_active, is_default, created_at) VALUES (:first, 'Go.Example.Test', true, false, now()), (:second, 'go.example.test', true, false, now())",
            lambda: {"first": uuid4(), "second": uuid4()},
            "domain normalization invariant",
        ),
        (
            "INSERT INTO domains (id, name, is_active, is_default, created_at) VALUES (:id, 'second-default.example.test', true, true, now())",
            lambda: {"id": uuid4()},
            "default domain invariant",
        ),
    ],
)
def test_user_domain_preflight_rejections_are_atomic(
    migration_database_url, statement, parameters, invariant
):
    run_alembic(migration_database_url, "upgrade", D6)
    engine = create_engine(migration_database_url)
    try:
        with engine.begin() as connection:
            connection.execute(text(statement), parameters())
        with pytest.raises(CalledProcessError) as exc_info:
            run_alembic(migration_database_url, "upgrade", "f31a8c0d4e72")
        assert invariant in f"{exc_info.value.stdout}\n{exc_info.value.stderr}"
        with engine.connect() as connection:
            assert (
                connection.scalar(text("SELECT version_num FROM alembic_version")) == D6
            )
            assert (
                connection.scalar(
                    text(
                        "SELECT count(*) FROM information_schema.columns WHERE table_name = 'domains' AND column_name = 'timezone'"
                    )
                )
                == 0
            )
    finally:
        engine.dispose()


@pytest.mark.parametrize("previous_revision", TASK_PREVIOUS_REVISIONS)
def test_final_head_round_trips_each_task_previous_revision_with_representable_rows(
    migration_database_url, previous_revision
):
    run_alembic(migration_database_url, "upgrade", D6)
    engine = create_engine(migration_database_url)
    try:
        with engine.begin() as connection:
            ids, expected_counts = _seed_representable_d6_rows(connection)
    finally:
        engine.dispose()
    run_alembic(migration_database_url, "upgrade", "head")
    run_alembic(migration_database_url, "downgrade", previous_revision)
    run_alembic(migration_database_url, "upgrade", "head")
    _assert_seed_ids_counts_and_conversions(
        migration_database_url, ids, expected_counts
    )


def test_final_database_matches_every_orm_column_default_nullability_check_and_fk(
    migration_database_url,
):
    run_alembic(migration_database_url, "upgrade", "head")
    contract = get_schema_contract(migration_database_url)
    columns = contract["columns"]
    checks = contract["checks"]
    foreign_keys = contract["foreign_keys"]
    for table_name, table in Base.metadata.tables.items():
        for column in table.columns:
            actual = columns[table_name][column.name]
            assert _normalized_type_name(actual["type"]) == _normalized_type_name(
                column.type
            )
            assert actual["timezone"] == getattr(column.type, "timezone", None)
            assert actual["nullable"] is column.nullable
            default = actual["default"]
            if column.server_default is None:
                assert default is None
            else:
                expected = str(column.server_default.arg).lower()
                assert expected.replace("'", "") in str(default).lower().replace(
                    "'", ""
                )
        expected_checks = {
            constraint.name
            for constraint in table.constraints
            if constraint.name and constraint.name.startswith("ck_")
        }
        assert expected_checks <= set(checks[table_name])
        expected_fks = {
            foreign_key.parent.name: (
                foreign_key.column.table.name,
                foreign_key.ondelete,
            )
            for constraint in table.foreign_key_constraints
            for foreign_key in constraint.elements
        }
        actual_fks = {
            facts["columns"][0]: (facts["referred_table"], facts["ondelete"])
            for facts in foreign_keys[table_name].values()
        }
        assert actual_fks == expected_fks


def test_alembic_check_has_no_new_upgrade_operations_at_final_head(
    migration_database_url,
):
    run_alembic(migration_database_url, "upgrade", "head")
    assert (
        "No new upgrade operations detected"
        in run_alembic(migration_database_url, "check").stdout
    )
