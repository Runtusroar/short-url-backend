"""Database contract for the access-rule semantics revision."""

import json
import uuid
from subprocess import CalledProcessError

import pytest
from sqlalchemy import create_engine, inspect, text

from tests.migrations.support import run_alembic

BASE_REVISION = "9b6d2f4a7c11"
REVISION = "de4c81b75920"


def _seed_link(connection, suffix: str) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
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
        {"id": user_id, "username": f"rules-{suffix}"},
    )
    connection.execute(
        text(
            """
            INSERT INTO domains (id, name, timezone, is_active, is_default, created_at, updated_at)
            VALUES (:id, :name, 'Asia/Shanghai', true, false, now(), now())
            """
        ),
        {"id": domain_id, "name": f"rules-{suffix}.example.com"},
    )
    connection.execute(
        text(
            """
            INSERT INTO short_links
                (id, domain_id, short_code, is_custom_alias, name, owner_id,
                 is_active, default_action, created_at, updated_at)
            VALUES (:id, :domain_id, :short_code, false, :name, :owner_id,
                    true, 'deny', now(), now())
            """
        ),
        {
            "id": link_id,
            "domain_id": domain_id,
            "short_code": f"rules-{suffix}",
            "name": f"rule migration {suffix}",
            "owner_id": user_id,
        },
    )
    return user_id, domain_id, link_id


def _seed_rule(
    connection,
    *,
    rule_id: uuid.UUID,
    link_id: uuid.UUID,
    priority: int,
    allow_bot: bool,
    allow_proxy: bool,
    referer_pattern: str | None = None,
    countries: str = '["cn", "US", "invalid"]',
    ua_platforms: str = '[" Mobile ", "BOT"]',
) -> None:
    connection.execute(
        text(
            """
            INSERT INTO access_rules
                (id, short_link_id, action, priority, countries, ua_platforms,
                 referer_pattern, allow_bot, allow_proxy, is_active)
            VALUES (:id, :link_id, 'allow', :priority, CAST(:countries AS jsonb),
                    CAST(:ua_platforms AS jsonb), :referer_pattern, :allow_bot,
                    :allow_proxy, true)
            """
        ),
        {
            "id": rule_id,
            "link_id": link_id,
            "priority": priority,
            "countries": countries,
            "ua_platforms": ua_platforms,
            "referer_pattern": referer_pattern,
            "allow_bot": allow_bot,
            "allow_proxy": allow_proxy,
        },
    )


def test_access_rule_semantics_migrate_and_representable_rows_round_trip(
    migration_database_url,
):
    run_alembic(migration_database_url, "upgrade", BASE_REVISION)
    engine = create_engine(migration_database_url)
    first_id = uuid.UUID("00000000-0000-0000-0000-000000000001")
    second_id = uuid.UUID("00000000-0000-0000-0000-000000000002")
    third_id = uuid.UUID("00000000-0000-0000-0000-000000000003")
    fourth_id = uuid.UUID("00000000-0000-0000-0000-000000000004")
    try:
        with engine.begin() as connection:
            _, _, duplicate_link_id = _seed_link(connection, "duplicates")
            _seed_rule(
                connection,
                rule_id=second_id,
                link_id=duplicate_link_id,
                priority=1,
                allow_bot=True,
                allow_proxy=True,
                referer_pattern=" https://one.example/*, , *social* ",
            )
            _seed_rule(
                connection,
                rule_id=first_id,
                link_id=duplicate_link_id,
                priority=1,
                allow_bot=False,
                allow_proxy=False,
            )
            _seed_rule(
                connection,
                rule_id=third_id,
                link_id=duplicate_link_id,
                priority=9,
                allow_bot=True,
                allow_proxy=False,
            )
            _seed_rule(
                connection,
                rule_id=fourth_id,
                link_id=duplicate_link_id,
                priority=10,
                allow_bot=False,
                allow_proxy=True,
            )
            _, _, stable_link_id = _seed_link(connection, "stable")
            stable_id = uuid.uuid4()
            _seed_rule(
                connection,
                rule_id=stable_id,
                link_id=stable_link_id,
                priority=42,
                allow_bot=False,
                allow_proxy=False,
                countries='["jp"]',
                ua_platforms='["PC"]',
            )

        run_alembic(migration_database_url, "upgrade", REVISION)

        with engine.connect() as connection:
            rows = (
                connection.execute(
                    text(
                        """
                    SELECT id, priority, name, client_requirement, proxy_requirement,
                           countries, ua_platforms, referer_patterns, created_at, updated_at
                    FROM access_rules
                    WHERE short_link_id = :link_id
                    ORDER BY priority, id
                    """
                    ),
                    {"link_id": duplicate_link_id},
                )
                .mappings()
                .all()
            )
            assert [(row["id"], row["priority"]) for row in rows] == [
                (first_id, 0),
                (second_id, 1),
                (third_id, 2),
                (fourth_id, 3),
            ]
            assert [
                (row["client_requirement"], row["proxy_requirement"]) for row in rows
            ] == [
                ("human", "non_proxy"),
                ("any", "any"),
                ("any", "non_proxy"),
                ("human", "any"),
            ]
            assert [row["name"] for row in rows] == [
                "Rule 0",
                "Rule 1",
                "Rule 2",
                "Rule 3",
            ]
            assert rows[1]["countries"] == ["CN", "US"]
            assert rows[1]["ua_platforms"] == ["mobile", "bot"]
            assert rows[1]["referer_patterns"] == ["https://one.example/*", "*social*"]
            assert all(
                row["created_at"] is not None and row["updated_at"] is not None
                for row in rows
            )
            stable = (
                connection.execute(
                    text(
                        """
                    SELECT priority, name, countries, ua_platforms, client_requirement, proxy_requirement
                    FROM access_rules WHERE id = :id
                    """
                    ),
                    {"id": stable_id},
                )
                .mappings()
                .one()
            )
            assert dict(stable) == {
                "priority": 42,
                "name": "Rule 42",
                "countries": ["JP"],
                "ua_platforms": ["pc"],
                "client_requirement": "human",
                "proxy_requirement": "non_proxy",
            }

        inspector = inspect(engine)
        columns = {
            column["name"]: column for column in inspector.get_columns("access_rules")
        }
        assert {
            "name",
            "client_requirement",
            "proxy_requirement",
            "referer_patterns",
            "created_at",
            "updated_at",
        } <= columns.keys()
        assert {"allow_bot", "allow_proxy", "referer_pattern"}.isdisjoint(columns)
        assert columns["name"]["nullable"] is False
        assert "gen_random_uuid" in columns["id"]["default"]
        assert columns["priority"]["default"] == "0"
        assert "[]" in columns["countries"]["default"]
        assert "[]" in columns["referer_patterns"]["default"]
        assert columns["client_requirement"]["default"] == "'any'::character varying"
        assert columns["proxy_requirement"]["default"] == "'any'::character varying"
        assert "now()" in columns["created_at"]["default"]
        assert "now()" in columns["updated_at"]["default"]
        checks = {
            check["name"] for check in inspector.get_check_constraints("access_rules")
        }
        assert {
            "ck_access_rules_action",
            "ck_access_rules_client_requirement",
            "ck_access_rules_proxy_requirement",
            "ck_access_rules_countries_array",
            "ck_access_rules_ua_platforms_array",
            "ck_access_rules_referer_patterns_array",
        } <= checks
        constraints = {
            constraint["name"]
            for constraint in inspector.get_unique_constraints("access_rules")
        }
        assert "uq_access_rules_link_priority" in constraints
        indexes = {
            index["name"]: index for index in inspector.get_indexes("access_rules")
        }
        assert indexes["idx_access_rules_link_active_priority"]["column_names"] == [
            "short_link_id",
            "priority",
            "id",
        ]
        assert (
            "is_active"
            in indexes["idx_access_rules_link_active_priority"]["dialect_options"][
                "postgresql_where"
            ]
        )
        access_rule_fks = {
            fk["constrained_columns"][0]: fk
            for fk in inspector.get_foreign_keys("access_rules")
        }
        assert access_rule_fks["short_link_id"]["name"] == "fk_access_rules_short_link"
        assert access_rule_fks["short_link_id"]["options"]["ondelete"] == "CASCADE"

        run_alembic(migration_database_url, "downgrade", BASE_REVISION)
        with engine.connect() as connection:
            restored = (
                connection.execute(
                    text(
                        """
                    SELECT id, priority, referer_pattern, allow_bot, allow_proxy
                    FROM access_rules WHERE short_link_id = :link_id ORDER BY priority, id
                    """
                    ),
                    {"link_id": duplicate_link_id},
                )
                .mappings()
                .all()
            )
            assert [(row["id"], row["priority"]) for row in restored] == [
                (first_id, 0),
                (second_id, 1),
                (third_id, 2),
                (fourth_id, 3),
            ]
            assert restored[1]["referer_pattern"] == "https://one.example/*,*social*"
            assert restored[0]["allow_bot"] is False
            assert restored[0]["allow_proxy"] is False
    finally:
        engine.dispose()


def test_access_rule_downgrade_rejects_unrepresentable_bot_or_proxy_rows(
    migration_database_url,
):
    run_alembic(migration_database_url, "upgrade", BASE_REVISION)
    engine = create_engine(migration_database_url)
    try:
        with engine.begin() as connection:
            _, _, link_id = _seed_link(connection, "downgrade")
            rule_id = uuid.uuid4()
            _seed_rule(
                connection,
                rule_id=rule_id,
                link_id=link_id,
                priority=0,
                allow_bot=True,
                allow_proxy=True,
            )
        run_alembic(migration_database_url, "upgrade", REVISION)
        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE access_rules SET client_requirement = 'bot' WHERE id = :id"
                ),
                {"id": rule_id},
            )

        with pytest.raises(CalledProcessError):
            run_alembic(migration_database_url, "downgrade", BASE_REVISION)

        with engine.connect() as connection:
            assert (
                connection.scalar(text("SELECT version_num FROM alembic_version"))
                == REVISION
            )
            assert (
                connection.scalar(
                    text("SELECT client_requirement FROM access_rules WHERE id = :id"),
                    {"id": rule_id},
                )
                == "bot"
            )
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    "referer_patterns",
    [
        ["x" * 256],
        ["x" * 128, "y" * 127],
    ],
    ids=["single-pattern-over-255", "joined-patterns-over-255"],
)
def test_access_rule_downgrade_rejects_referer_patterns_too_long_before_writes(
    migration_database_url, referer_patterns
):
    """A legal Task 3 JSON array must not be silently truncated for 9b."""
    run_alembic(migration_database_url, "upgrade", BASE_REVISION)
    engine = create_engine(migration_database_url)
    try:
        with engine.begin() as connection:
            _, _, link_id = _seed_link(connection, "referer-overflow")
            rule_id = uuid.uuid4()
            _seed_rule(
                connection,
                rule_id=rule_id,
                link_id=link_id,
                priority=0,
                allow_bot=True,
                allow_proxy=True,
            )
        run_alembic(migration_database_url, "upgrade", REVISION)
        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE access_rules SET referer_patterns = CAST(:patterns AS jsonb) "
                    "WHERE id = :id"
                ),
                {"id": rule_id, "patterns": json.dumps(referer_patterns)},
            )

        with pytest.raises(CalledProcessError) as raised:
            run_alembic(migration_database_url, "downgrade", BASE_REVISION)

        diagnostic = f"{raised.value.stdout}\n{raised.value.stderr}"
        assert "referer pattern downgrade invariant" in diagnostic
        assert referer_patterns[0] not in diagnostic
        assert "value too long" not in diagnostic
        assert "psycopg" not in diagnostic
        assert "sqlalchemy.exc" not in diagnostic
        with engine.connect() as connection:
            assert (
                connection.scalar(text("SELECT version_num FROM alembic_version"))
                == REVISION
            )
            columns = {
                column_info["name"]
                for column_info in inspect(engine).get_columns("access_rules")
            }
            assert {"referer_pattern", "allow_bot", "allow_proxy"}.isdisjoint(columns)
            assert {
                "name",
                "client_requirement",
                "proxy_requirement",
                "referer_patterns",
            } <= columns
            assert (
                connection.scalar(
                    text("SELECT referer_patterns FROM access_rules WHERE id = :id"),
                    {"id": rule_id},
                )
                == referer_patterns
            )
    finally:
        engine.dispose()


def test_access_rule_downgrade_rejects_non_string_referer_pattern_before_writes(
    migration_database_url,
):
    """The JSON-array check permits `[1]`, but 9b cannot represent it."""
    run_alembic(migration_database_url, "upgrade", BASE_REVISION)
    engine = create_engine(migration_database_url)
    try:
        with engine.begin() as connection:
            _, _, link_id = _seed_link(connection, "referer-non-string")
            rule_id = uuid.uuid4()
            _seed_rule(
                connection,
                rule_id=rule_id,
                link_id=link_id,
                priority=0,
                allow_bot=True,
                allow_proxy=True,
            )
        run_alembic(migration_database_url, "upgrade", REVISION)
        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE access_rules SET referer_patterns = '[1]'::jsonb WHERE id = :id"
                ),
                {"id": rule_id},
            )

        with pytest.raises(CalledProcessError) as raised:
            run_alembic(migration_database_url, "downgrade", BASE_REVISION)

        diagnostic = f"{raised.value.stdout}\n{raised.value.stderr}"
        assert "referer pattern downgrade invariant" in diagnostic
        assert "[1]" not in diagnostic
        assert "TypeError" not in diagnostic
        assert "psycopg" not in diagnostic
        assert "sqlalchemy.exc" not in diagnostic
        with engine.connect() as connection:
            assert (
                connection.scalar(text("SELECT version_num FROM alembic_version"))
                == REVISION
            )
            columns = {
                column_info["name"]
                for column_info in inspect(engine).get_columns("access_rules")
            }
            assert {"referer_pattern", "allow_bot", "allow_proxy"}.isdisjoint(columns)
            assert {
                "name",
                "client_requirement",
                "proxy_requirement",
                "referer_patterns",
            } <= columns
            assert connection.scalar(
                text("SELECT referer_patterns FROM access_rules WHERE id = :id"),
                {"id": rule_id},
            ) == [1]
    finally:
        engine.dispose()


def test_access_rule_downgrade_preserves_255_character_referer_pattern(
    migration_database_url,
):
    run_alembic(migration_database_url, "upgrade", BASE_REVISION)
    engine = create_engine(migration_database_url)
    boundary_pattern = "x" * 255
    try:
        with engine.begin() as connection:
            _, _, link_id = _seed_link(connection, "referer-boundary")
            rule_id = uuid.uuid4()
            _seed_rule(
                connection,
                rule_id=rule_id,
                link_id=link_id,
                priority=0,
                allow_bot=True,
                allow_proxy=True,
            )
        run_alembic(migration_database_url, "upgrade", REVISION)
        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE access_rules SET referer_patterns = CAST(:patterns AS jsonb) "
                    "WHERE id = :id"
                ),
                {"id": rule_id, "patterns": json.dumps([boundary_pattern])},
            )

        run_alembic(migration_database_url, "downgrade", BASE_REVISION)
        with engine.connect() as connection:
            assert (
                connection.scalar(
                    text("SELECT referer_pattern FROM access_rules WHERE id = :id"),
                    {"id": rule_id},
                )
                == boundary_pattern
            )

        run_alembic(migration_database_url, "upgrade", REVISION)
        with engine.connect() as connection:
            assert connection.scalar(
                text("SELECT referer_patterns FROM access_rules WHERE id = :id"),
                {"id": rule_id},
            ) == [boundary_pattern]
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    ("column", "legacy_json", "invariant"),
    [
        ("action", None, "access rule action invariant violated"),
        ("countries", '{"CN": true}', "access rule countries invariant violated"),
        ("countries", "null", "access rule countries invariant violated"),
        ("ua_platforms", "null", "access rule ua platforms invariant violated"),
    ],
)
def test_access_rule_upgrade_rejects_unsafe_legacy_values_before_schema_writes(
    migration_database_url, column, legacy_json, invariant
):
    run_alembic(migration_database_url, "upgrade", BASE_REVISION)
    engine = create_engine(migration_database_url)
    sentinel = "badaction"
    try:
        with engine.begin() as connection:
            _, _, link_id = _seed_link(
                connection, f"invalid-{column.replace('_', '-')}"
            )
            rule_id = uuid.uuid4()
            _seed_rule(
                connection,
                rule_id=rule_id,
                link_id=link_id,
                priority=0,
                allow_bot=True,
                allow_proxy=True,
            )
            if column == "action":
                connection.execute(
                    text("UPDATE access_rules SET action = :value WHERE id = :id"),
                    {"id": rule_id, "value": sentinel},
                )
            else:
                connection.execute(
                    text(
                        f"UPDATE access_rules SET {column} = CAST(:value AS jsonb) WHERE id = :id"
                    ),
                    {"id": rule_id, "value": legacy_json},
                )

        with pytest.raises(CalledProcessError) as raised:
            run_alembic(migration_database_url, "upgrade", REVISION)

        diagnostic = f"{raised.value.stdout}\n{raised.value.stderr}"
        assert invariant in diagnostic
        assert sentinel not in diagnostic
        with engine.connect() as connection:
            assert (
                connection.scalar(text("SELECT version_num FROM alembic_version"))
                == BASE_REVISION
            )
            columns = {
                column_info["name"]
                for column_info in inspect(engine).get_columns("access_rules")
            }
            assert {
                "name",
                "client_requirement",
                "proxy_requirement",
                "referer_patterns",
            }.isdisjoint(columns)
            assert connection.scalar(
                text("SELECT action FROM access_rules WHERE id = :id"), {"id": rule_id}
            ) == (sentinel if column == "action" else "allow")
    finally:
        engine.dispose()


def test_access_rule_downgrade_restores_9b_defaults_and_direct_insert_contract(
    migration_database_url,
):
    run_alembic(migration_database_url, "upgrade", BASE_REVISION)
    engine = create_engine(migration_database_url)
    try:
        parent_columns = {
            column["name"]: column
            for column in inspect(engine).get_columns("access_rules")
        }
        assert "gen_random_uuid" in parent_columns["id"]["default"]
        assert parent_columns["priority"]["default"] is None
        assert parent_columns["countries"]["default"] is None
        assert parent_columns["ua_platforms"]["default"] is None
        assert parent_columns["is_active"]["default"] is None
        assert "true" in parent_columns["allow_bot"]["default"]
        assert "true" in parent_columns["allow_proxy"]["default"]
        assert {"created_at", "updated_at"}.isdisjoint(parent_columns)

        with engine.begin() as connection:
            _, _, link_id = _seed_link(connection, "defaults")
            _seed_rule(
                connection,
                rule_id=uuid.uuid4(),
                link_id=link_id,
                priority=0,
                allow_bot=True,
                allow_proxy=True,
            )

        run_alembic(migration_database_url, "upgrade", REVISION)
        run_alembic(migration_database_url, "downgrade", BASE_REVISION)

        restored_columns = {
            column["name"]: column
            for column in inspect(engine).get_columns("access_rules")
        }
        for column_name in ("id", "priority", "countries", "ua_platforms", "is_active"):
            assert (
                restored_columns[column_name]["default"]
                == parent_columns[column_name]["default"]
            )
        assert "true" in restored_columns["allow_bot"]["default"]
        assert "true" in restored_columns["allow_proxy"]["default"]
        assert {"created_at", "updated_at"}.isdisjoint(restored_columns)

        with engine.begin() as connection:
            inserted = (
                connection.execute(
                    text(
                        """
                    INSERT INTO access_rules
                        (short_link_id, action, priority, countries, ua_platforms, is_active)
                    VALUES (:short_link_id, 'allow', 7, '[]'::jsonb, '[]'::jsonb, true)
                    RETURNING id, allow_bot, allow_proxy, countries, ua_platforms
                    """
                    ),
                    {"short_link_id": link_id},
                )
                .mappings()
                .one()
            )
            assert inserted["id"] is not None
            assert inserted["allow_bot"] is True
            assert inserted["allow_proxy"] is True
            assert inserted["countries"] == []
            assert inserted["ua_platforms"] == []

        run_alembic(migration_database_url, "upgrade", REVISION)
        assert inspect(engine).get_columns("access_rules")
    finally:
        engine.dispose()
