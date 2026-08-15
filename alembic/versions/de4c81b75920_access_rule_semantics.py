"""replace access-rule boolean semantics with explicit requirements

Revision ID: de4c81b75920
Revises: 9b6d2f4a7c11
"""

import json
from collections import defaultdict

import sqlalchemy as sa
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "de4c81b75920"
down_revision = "9b6d2f4a7c11"
branch_labels = None
depends_on = None


def _normalize_countries(values: object) -> list[str]:
    if not isinstance(values, list):
        return []
    normalized: list[str] = []
    for value in values:
        if not isinstance(value, str):
            continue
        country = value.strip().upper()
        if len(country) == 2 and country.isalpha():
            normalized.append(country)
    return normalized


def _normalize_platforms(values: object) -> list[str]:
    if not isinstance(values, list):
        return []
    return [
        platform.strip().lower()
        for platform in values
        if isinstance(platform, str) and platform.strip()
    ]


def _normalize_referers(value: object) -> list[str]:
    if not isinstance(value, str):
        return []
    return [pattern.strip() for pattern in value.split(",") if pattern.strip()]


def _replace_short_link_foreign_key(constraint_name: str) -> None:
    foreign_keys = inspect(op.get_bind()).get_foreign_keys("access_rules")
    for foreign_key in foreign_keys:
        if foreign_key["constrained_columns"] == ["short_link_id"]:
            name = foreign_key["name"]
            if not isinstance(name, str) or not name:
                raise RuntimeError("access rule foreign key has no inspectable name")
            op.drop_constraint(name, "access_rules", type_="foreignkey")
            break
    op.create_foreign_key(
        constraint_name,
        "access_rules",
        "short_links",
        ["short_link_id"],
        ["id"],
        ondelete="CASCADE",
    )


def _set_server_defaults() -> None:
    for column_name, column_type, default in (
        ("id", postgresql.UUID(as_uuid=True), "gen_random_uuid()"),
        ("priority", sa.Integer(), "0"),
        ("countries", postgresql.JSONB(), "'[]'::jsonb"),
        ("ua_platforms", postgresql.JSONB(), "'[]'::jsonb"),
        ("referer_patterns", postgresql.JSONB(), "'[]'::jsonb"),
        ("client_requirement", sa.String(length=16), "'any'"),
        ("proxy_requirement", sa.String(length=16), "'any'"),
        ("is_active", sa.Boolean(), "true"),
        ("created_at", sa.DateTime(timezone=True), "now()"),
        ("updated_at", sa.DateTime(timezone=True), "now()"),
    ):
        op.alter_column(
            "access_rules",
            column_name,
            existing_type=column_type,
            existing_nullable=False,
            server_default=sa.text(default),
        )


def upgrade() -> None:
    op.add_column(
        "access_rules", sa.Column("name", sa.String(length=128), nullable=True)
    )
    op.add_column(
        "access_rules", sa.Column("referer_patterns", postgresql.JSONB(), nullable=True)
    )
    op.add_column(
        "access_rules",
        sa.Column("client_requirement", sa.String(length=16), nullable=True),
    )
    op.add_column(
        "access_rules",
        sa.Column("proxy_requirement", sa.String(length=16), nullable=True),
    )
    op.add_column(
        "access_rules",
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "access_rules",
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )

    rows = (
        op.get_bind()
        .execute(
            sa.text(
                """
            SELECT id, short_link_id, priority, countries, ua_platforms, referer_pattern,
                   allow_bot, allow_proxy
            FROM access_rules
            """
            )
        )
        .mappings()
        .all()
    )
    by_link: dict[object, list[dict[str, object]]] = defaultdict(list)
    for row in rows:
        by_link[row["short_link_id"]].append(dict(row))

    for link_rows in by_link.values():
        has_duplicate_priority = len({row["priority"] for row in link_rows}) != len(
            link_rows
        )
        ordered_rows = sorted(
            link_rows, key=lambda row: (row["priority"], str(row["id"]))
        )
        for position, row in enumerate(ordered_rows):
            priority = position if has_duplicate_priority else row["priority"]
            client_requirement = "any" if row["allow_bot"] else "human"
            proxy_requirement = "any" if row["allow_proxy"] else "non_proxy"
            op.get_bind().execute(
                sa.text(
                    """
                    UPDATE access_rules
                    SET priority = :priority,
                        name = :name,
                        countries = CAST(:countries AS jsonb),
                        ua_platforms = CAST(:ua_platforms AS jsonb),
                        referer_patterns = CAST(:referer_patterns AS jsonb),
                        client_requirement = :client_requirement,
                        proxy_requirement = :proxy_requirement,
                        created_at = now(),
                        updated_at = now()
                    WHERE id = :id
                    """
                ),
                {
                    "id": row["id"],
                    "priority": priority,
                    "name": f"Rule {priority}",
                    "countries": json.dumps(_normalize_countries(row["countries"])),
                    "ua_platforms": json.dumps(
                        _normalize_platforms(row["ua_platforms"])
                    ),
                    "referer_patterns": json.dumps(
                        _normalize_referers(row["referer_pattern"])
                    ),
                    "client_requirement": client_requirement,
                    "proxy_requirement": proxy_requirement,
                },
            )

    op.execute(
        "UPDATE access_rules SET created_at = now(), updated_at = now() WHERE created_at IS NULL"
    )
    op.alter_column(
        "access_rules", "name", existing_type=sa.String(length=128), nullable=False
    )
    for column_name, column_type in (
        ("countries", postgresql.JSONB()),
        ("ua_platforms", postgresql.JSONB()),
        ("referer_patterns", postgresql.JSONB()),
        ("client_requirement", sa.String(length=16)),
        ("proxy_requirement", sa.String(length=16)),
        ("created_at", sa.DateTime(timezone=True)),
        ("updated_at", sa.DateTime(timezone=True)),
    ):
        op.alter_column(
            "access_rules", column_name, existing_type=column_type, nullable=False
        )
    _set_server_defaults()

    op.create_check_constraint(
        "ck_access_rules_action", "access_rules", "action IN ('allow', 'deny')"
    )
    op.create_check_constraint(
        "ck_access_rules_client_requirement",
        "access_rules",
        "client_requirement IN ('any', 'human', 'bot')",
    )
    op.create_check_constraint(
        "ck_access_rules_proxy_requirement",
        "access_rules",
        "proxy_requirement IN ('any', 'non_proxy', 'proxy')",
    )
    op.create_check_constraint(
        "ck_access_rules_countries_array",
        "access_rules",
        "jsonb_typeof(countries) = 'array'",
    )
    op.create_check_constraint(
        "ck_access_rules_ua_platforms_array",
        "access_rules",
        "jsonb_typeof(ua_platforms) = 'array'",
    )
    op.create_check_constraint(
        "ck_access_rules_referer_patterns_array",
        "access_rules",
        "jsonb_typeof(referer_patterns) = 'array'",
    )
    op.create_unique_constraint(
        "uq_access_rules_link_priority", "access_rules", ["short_link_id", "priority"]
    )
    op.create_index(
        "idx_access_rules_link_active_priority",
        "access_rules",
        ["short_link_id", "priority", "id"],
        unique=False,
        postgresql_where=sa.text("is_active"),
    )
    _replace_short_link_foreign_key("fk_access_rules_short_link")

    op.drop_column("access_rules", "allow_bot")
    op.drop_column("access_rules", "allow_proxy")
    op.drop_column("access_rules", "referer_pattern")


def downgrade() -> None:
    unrepresentable_count = int(
        op.get_bind()
        .execute(
            sa.text(
                """
                SELECT count(*) FROM access_rules
                WHERE client_requirement = 'bot' OR proxy_requirement = 'proxy'
                """
            )
        )
        .scalar_one()
    )
    if unrepresentable_count:
        raise RuntimeError("access rule downgrade representability invariant violated")

    op.add_column(
        "access_rules",
        sa.Column("referer_pattern", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "access_rules",
        sa.Column(
            "allow_proxy", sa.Boolean(), nullable=True, server_default=sa.text("true")
        ),
    )
    op.add_column(
        "access_rules",
        sa.Column(
            "allow_bot", sa.Boolean(), nullable=True, server_default=sa.text("true")
        ),
    )
    rows = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT id, referer_patterns, client_requirement, proxy_requirement FROM access_rules"
            )
        )
        .mappings()
        .all()
    )
    for row in rows:
        referer_patterns = (
            row["referer_patterns"] if isinstance(row["referer_patterns"], list) else []
        )
        op.get_bind().execute(
            sa.text(
                """
                UPDATE access_rules
                SET referer_pattern = :referer_pattern,
                    allow_bot = :allow_bot,
                    allow_proxy = :allow_proxy
                WHERE id = :id
                """
            ),
            {
                "id": row["id"],
                "referer_pattern": ",".join(referer_patterns),
                "allow_bot": row["client_requirement"] != "human",
                "allow_proxy": row["proxy_requirement"] != "non_proxy",
            },
        )
    for column_name in ("allow_bot", "allow_proxy"):
        op.alter_column(
            "access_rules",
            column_name,
            existing_type=sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        )

    op.drop_index("idx_access_rules_link_active_priority", table_name="access_rules")
    op.drop_constraint("uq_access_rules_link_priority", "access_rules", type_="unique")
    for constraint_name in (
        "ck_access_rules_referer_patterns_array",
        "ck_access_rules_ua_platforms_array",
        "ck_access_rules_countries_array",
        "ck_access_rules_proxy_requirement",
        "ck_access_rules_client_requirement",
        "ck_access_rules_action",
    ):
        op.drop_constraint(constraint_name, "access_rules", type_="check")
    _replace_short_link_foreign_key("access_rules_short_link_id_fkey")
    op.drop_column("access_rules", "updated_at")
    op.drop_column("access_rules", "created_at")
    op.drop_column("access_rules", "proxy_requirement")
    op.drop_column("access_rules", "client_requirement")
    op.drop_column("access_rules", "referer_patterns")
    op.drop_column("access_rules", "name")
