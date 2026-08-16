"""canonical short codes and PostgreSQL log-search indexes

Revision ID: c8e4f1a26b73
Revises: a73f0b9d4216
"""

import sqlalchemy as sa

from alembic import op

revision = "c8e4f1a26b73"
down_revision = "a73f0b9d4216"
branch_labels = None
depends_on = None


def _preflight_short_codes() -> None:
    connection = op.get_bind()
    invalid = connection.scalar(
        sa.text(
            "SELECT count(*) FROM short_links "
            "WHERE lower(btrim(short_code)) !~ '^[a-z0-9_-]{3,32}$'"
        )
    )
    if invalid:
        raise RuntimeError("short code canonicalization invariant")
    collisions = connection.scalar(
        sa.text(
            "SELECT count(*) FROM ("
            "SELECT domain_id, lower(btrim(short_code)) "
            "FROM short_links GROUP BY domain_id, lower(btrim(short_code)) "
            "HAVING count(*) > 1) AS conflicts"
        )
    )
    if collisions:
        raise RuntimeError("short code normalization collision invariant")


def _create_phase5_log_indexes() -> None:
    op.create_index(
        "idx_access_logs_domain_accessed_at",
        "access_logs",
        ["domain_id", sa.text("accessed_at DESC"), sa.text("id DESC")],
    )
    op.create_index(
        "idx_access_logs_link_accessed_at",
        "access_logs",
        ["short_link_id", sa.text("accessed_at DESC"), sa.text("id DESC")],
    )
    op.create_index(
        "idx_access_logs_domain_result_accessed_at",
        "access_logs",
        ["domain_id", "result", sa.text("accessed_at DESC"), sa.text("id DESC")],
    )
    op.create_index(
        "idx_access_logs_domain_country_accessed_at",
        "access_logs",
        ["domain_id", "country", sa.text("accessed_at DESC"), sa.text("id DESC")],
    )


def _create_phase4_log_indexes() -> None:
    op.create_index(
        "idx_access_logs_domain_accessed_at",
        "access_logs",
        ["domain_id", sa.text("accessed_at DESC")],
    )
    op.create_index(
        "idx_access_logs_link_accessed_at",
        "access_logs",
        ["short_link_id", sa.text("accessed_at DESC")],
    )
    op.create_index(
        "idx_access_logs_domain_result_accessed_at",
        "access_logs",
        ["domain_id", "result", sa.text("accessed_at DESC")],
    )
    op.create_index(
        "idx_access_logs_domain_country_accessed_at",
        "access_logs",
        ["domain_id", "country", sa.text("accessed_at DESC")],
    )


def upgrade() -> None:
    _preflight_short_codes()
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute("UPDATE short_links SET short_code = lower(btrim(short_code))")
    op.create_check_constraint(
        "ck_short_links_short_code_canonical",
        "short_links",
        "short_code = lower(btrim(short_code)) AND short_code ~ '^[a-z0-9_-]{3,32}$'",
    )
    op.create_index(
        "idx_short_links_name_trgm",
        "short_links",
        [sa.text("lower(name) gin_trgm_ops")],
        postgresql_using="gin",
    )
    op.create_index(
        "idx_short_links_domain_code_pattern",
        "short_links",
        ["domain_id", sa.text("short_code varchar_pattern_ops")],
    )
    for name in (
        "idx_access_logs_domain_accessed_at",
        "idx_access_logs_link_accessed_at",
        "idx_access_logs_domain_result_accessed_at",
        "idx_access_logs_domain_country_accessed_at",
    ):
        op.drop_index(name, table_name="access_logs")
    _create_phase5_log_indexes()


def downgrade() -> None:
    for name in (
        "idx_access_logs_domain_accessed_at",
        "idx_access_logs_link_accessed_at",
        "idx_access_logs_domain_result_accessed_at",
        "idx_access_logs_domain_country_accessed_at",
    ):
        op.drop_index(name, table_name="access_logs")
    _create_phase4_log_indexes()
    op.drop_index("idx_short_links_domain_code_pattern", table_name="short_links")
    op.drop_index("idx_short_links_name_trgm", table_name="short_links")
    op.drop_constraint(
        "ck_short_links_short_code_canonical", "short_links", type_="check"
    )
