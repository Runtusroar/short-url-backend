"""add short-link, target, and permission lifecycle controls

Revision ID: 9b6d2f4a7c11
Revises: f31a8c0d4e72
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql


revision = "9b6d2f4a7c11"
down_revision = "f31a8c0d4e72"
branch_labels = None
depends_on = None


def _count(statement: str) -> int:
    return int(op.get_bind().execute(sa.text(statement)).scalar_one())


def _reject_invalid_legacy_data() -> None:
    """Reject data that cannot satisfy the new checks before changing schema or rows."""
    if _count(
        """
        SELECT count(*) FROM short_links
        WHERE description IS NOT NULL
          AND btrim(description) <> ''
          AND length(description) > 128
        """
    ):
        raise RuntimeError("short link name length invariant violated")
    if _count(
        "SELECT count(*) FROM short_links WHERE default_action NOT IN ('allow', 'deny')"
    ):
        raise RuntimeError("short link default action invariant violated")
    if _count(
        "SELECT count(*) FROM target_urls WHERE url_type NOT IN ('allowed', 'denied')"
    ):
        raise RuntimeError("target URL type invariant violated")


def _replace_foreign_keys(
    table_name: str,
    specifications: list[tuple[str, str, str, str]],
) -> None:
    """Replace named foreign keys for the listed local columns with explicit actions."""
    foreign_keys = inspect(op.get_bind()).get_foreign_keys(table_name)
    columns = {local_column for local_column, _, _, _ in specifications}
    for foreign_key in foreign_keys:
        constrained_columns = foreign_key["constrained_columns"]
        if len(constrained_columns) != 1 or constrained_columns[0] not in columns:
            continue
        name = foreign_key["name"]
        if not isinstance(name, str) or not name:
            raise RuntimeError(f"{table_name} foreign key has no inspectable name")
        op.drop_constraint(name, table_name, type_="foreignkey")
    for local_column, referred_table, constraint_name, ondelete in specifications:
        op.create_foreign_key(
            constraint_name,
            table_name,
            referred_table,
            [local_column],
            ["id"],
            ondelete=ondelete,
        )


def _set_server_defaults() -> None:
    for table_name, columns in {
        "short_links": {
            "id": "gen_random_uuid()",
            "is_custom_alias": "false",
            "is_active": "true",
            "default_action": "'deny'",
            "created_at": "now()",
            "updated_at": "now()",
        },
        "short_link_permissions": {
            "id": "gen_random_uuid()",
            "created_at": "now()",
        },
        "target_urls": {
            "id": "gen_random_uuid()",
            "weight": "1",
            "is_active": "true",
            "created_at": "now()",
            "updated_at": "now()",
        },
    }.items():
        for column_name, default in columns.items():
            op.alter_column(
                table_name,
                column_name,
                existing_nullable=False,
                server_default=sa.text(default),
            )


def upgrade() -> None:
    _reject_invalid_legacy_data()

    op.add_column("short_links", sa.Column("name", sa.String(length=128), nullable=True))
    op.execute(
        """
        UPDATE short_links
        SET name = COALESCE(NULLIF(btrim(description), ''), short_code)
        """
    )
    op.alter_column("short_links", "name", existing_type=sa.String(length=128), nullable=False)
    op.drop_column("short_links", "description")

    op.add_column(
        "short_links", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "short_links", sa.Column("deleted_by", postgresql.UUID(as_uuid=True), nullable=True)
    )
    _replace_foreign_keys(
        "short_links",
        [
            ("domain_id", "domains", "fk_short_links_domain", "RESTRICT"),
            ("owner_id", "users", "fk_short_links_owner", "RESTRICT"),
        ],
    )
    op.create_foreign_key(
        "fk_short_links_deleted_by",
        "short_links",
        "users",
        ["deleted_by"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_check_constraint(
        "ck_short_links_default_action",
        "short_links",
        "default_action IN ('allow', 'deny')",
    )
    op.create_check_constraint(
        "ck_short_links_deleted_actor",
        "short_links",
        "deleted_by IS NULL OR deleted_at IS NOT NULL",
    )
    op.create_index(
        "idx_short_links_domain_created_at",
        "short_links",
        ["domain_id", sa.text("created_at DESC")],
        unique=False,
    )
    op.create_index(
        "idx_short_links_domain_owner_created_at",
        "short_links",
        ["domain_id", "owner_id", sa.text("created_at DESC")],
        unique=False,
    )

    op.add_column(
        "short_link_permissions",
        sa.Column("granted_by", postgresql.UUID(as_uuid=True), nullable=True),
    )
    _replace_foreign_keys(
        "short_link_permissions",
        [
            ("short_link_id", "short_links", "fk_short_link_permissions_link", "RESTRICT"),
            ("user_id", "users", "fk_short_link_permissions_user", "RESTRICT"),
        ],
    )
    op.create_foreign_key(
        "fk_short_link_permissions_granted_by",
        "short_link_permissions",
        "users",
        ["granted_by"],
        ["id"],
        ondelete="RESTRICT",
    )

    op.add_column("target_urls", sa.Column("name", sa.String(length=128), nullable=True))
    op.add_column(
        "target_urls",
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.execute("UPDATE target_urls SET weight = 1, is_active = false WHERE weight < 1")
    op.create_check_constraint(
        "ck_target_urls_type", "target_urls", "url_type IN ('allowed', 'denied')"
    )
    op.create_check_constraint("ck_target_urls_weight", "target_urls", "weight >= 1")

    _set_server_defaults()


def downgrade() -> None:
    op.drop_constraint("ck_target_urls_weight", "target_urls", type_="check")
    op.drop_constraint("ck_target_urls_type", "target_urls", type_="check")
    op.drop_column("target_urls", "updated_at")
    op.drop_column("target_urls", "name")

    op.drop_constraint(
        "fk_short_link_permissions_granted_by",
        "short_link_permissions",
        type_="foreignkey",
    )
    _replace_foreign_keys(
        "short_link_permissions",
        [
            ("short_link_id", "short_links", "short_link_permissions_short_link_id_fkey", "CASCADE"),
            ("user_id", "users", "short_link_permissions_user_id_fkey", "CASCADE"),
        ],
    )
    op.drop_column("short_link_permissions", "granted_by")

    op.drop_index("idx_short_links_domain_owner_created_at", table_name="short_links")
    op.drop_index("idx_short_links_domain_created_at", table_name="short_links")
    op.drop_constraint("ck_short_links_deleted_actor", "short_links", type_="check")
    op.drop_constraint("ck_short_links_default_action", "short_links", type_="check")
    op.drop_constraint("fk_short_links_deleted_by", "short_links", type_="foreignkey")
    _replace_foreign_keys(
        "short_links",
        [
            ("domain_id", "domains", "fk_short_links_domain", "NO ACTION"),
            ("owner_id", "users", "short_links_owner_id_fkey", "NO ACTION"),
        ],
    )
    op.drop_column("short_links", "deleted_by")
    op.drop_column("short_links", "deleted_at")
    op.add_column("short_links", sa.Column("description", sa.Text(), nullable=True))
    op.execute("UPDATE short_links SET description = name")
    op.drop_column("short_links", "name")

    for table_name, columns in {
        "short_links": ("id", "is_custom_alias", "is_active", "default_action", "created_at", "updated_at"),
        "short_link_permissions": ("id", "created_at"),
        "target_urls": ("id", "weight", "is_active", "created_at"),
    }.items():
        for column_name in columns:
            op.alter_column(table_name, column_name, existing_nullable=False, server_default=None)
