"""add user, domain, and domain-grant lifecycle controls

Revision ID: f31a8c0d4e72
Revises: d6e8f0a21b35
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql


revision = "f31a8c0d4e72"
down_revision = "d6e8f0a21b35"
branch_labels = None
depends_on = None


_HOST_PATTERN = r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)*$"


def _count(statement: str) -> int:
    return int(op.get_bind().execute(sa.text(statement)).scalar_one())


def _reject_invalid_legacy_data() -> None:
    if _count(
        """
        SELECT count(*)
        FROM (
            SELECT lower(btrim(username))
            FROM users
            GROUP BY lower(btrim(username))
            HAVING count(*) > 1
        ) AS duplicates
        """
    ):
        raise RuntimeError("username normalization invariant violated")
    if _count(
        """
        SELECT count(*)
        FROM (
            SELECT lower(btrim(name))
            FROM domains
            GROUP BY lower(btrim(name))
            HAVING count(*) > 1
        ) AS duplicates
        """
    ):
        raise RuntimeError("domain normalization invariant violated")
    if _count("SELECT count(*) FROM domains WHERE is_default") > 1:
        raise RuntimeError("default domain invariant violated")
    if _count(
        f"""
        SELECT count(*)
        FROM domains
        WHERE length(lower(btrim(name))) > 253
           OR lower(btrim(name)) !~ '{_HOST_PATTERN}'
        """
    ):
        raise RuntimeError("domain host invariant violated")
    if _count(
        """
        SELECT count(*)
        FROM users
        WHERE role NOT IN ('admin', 'operator', 'client')
        """
    ):
        raise RuntimeError("user role invariant violated")


def _replace_user_domain_foreign_keys(
    user_ondelete: str, domain_ondelete: str
) -> None:
    foreign_keys = inspect(op.get_bind()).get_foreign_keys("user_domains")
    for foreign_key in foreign_keys:
        constrained_columns = foreign_key["constrained_columns"]
        if constrained_columns not in (["user_id"], ["domain_id"]):
            continue
        name = foreign_key["name"]
        if not isinstance(name, str) or not name:
            raise RuntimeError("user domain foreign key has no inspectable name")
        op.drop_constraint(name, "user_domains", type_="foreignkey")
    op.create_foreign_key(
        "fk_user_domains_user",
        "user_domains",
        "users",
        ["user_id"],
        ["id"],
        ondelete=user_ondelete,
    )
    op.create_foreign_key(
        "fk_user_domains_domain",
        "user_domains",
        "domains",
        ["domain_id"],
        ["id"],
        ondelete=domain_ondelete,
    )


def upgrade() -> None:
    _reject_invalid_legacy_data()

    op.execute("UPDATE users SET username = lower(btrim(username))")
    op.execute("UPDATE domains SET name = lower(btrim(name))")

    op.alter_column(
        "users",
        "is_active",
        existing_type=sa.Boolean(),
        existing_nullable=False,
        server_default=sa.text("true"),
    )
    for column_name in ("created_at", "updated_at"):
        op.alter_column(
            "users",
            column_name,
            existing_type=sa.DateTime(timezone=True),
            existing_nullable=False,
            server_default=sa.text("now()"),
        )

    op.add_column(
        "domains",
        sa.Column(
            "timezone",
            sa.String(length=64),
            nullable=False,
            server_default=sa.text("'Asia/Shanghai'"),
        ),
    )
    op.add_column(
        "domains",
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    for column_name, default in (("is_active", "true"), ("is_default", "false")):
        op.alter_column(
            "domains",
            column_name,
            existing_type=sa.Boolean(),
            existing_nullable=False,
            server_default=sa.text(default),
        )
    op.alter_column(
        "domains",
        "created_at",
        existing_type=sa.DateTime(timezone=True),
        existing_nullable=False,
        server_default=sa.text("now()"),
    )

    op.add_column(
        "user_domains",
        sa.Column("granted_by", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_user_domains_granted_by",
        "user_domains",
        "users",
        ["granted_by"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index("idx_user_domains_domain", "user_domains", ["domain_id"], unique=False)
    op.alter_column(
        "user_domains",
        "created_at",
        existing_type=sa.DateTime(timezone=True),
        existing_nullable=False,
        server_default=sa.text("now()"),
    )
    _replace_user_domain_foreign_keys("RESTRICT", "RESTRICT")

    op.create_check_constraint(
        "ck_users_role",
        "users",
        "role IN ('admin', 'operator', 'client')",
    )
    op.create_check_constraint(
        "ck_users_username_lower",
        "users",
        "username = lower(btrim(username))",
    )
    op.create_check_constraint(
        "ck_domains_name_lower_host",
        "domains",
        f"name = lower(btrim(name)) AND length(name) <= 253 AND name ~ '{_HOST_PATTERN}'",
    )
    op.create_index(
        "uq_domains_one_default",
        "domains",
        ["is_default"],
        unique=True,
        postgresql_where=sa.text("is_default"),
    )


def downgrade() -> None:
    op.drop_index("uq_domains_one_default", table_name="domains", if_exists=True)
    op.drop_constraint("ck_domains_name_lower_host", "domains", type_="check")
    op.drop_constraint("ck_users_username_lower", "users", type_="check")
    op.drop_constraint("ck_users_role", "users", type_="check")

    op.drop_constraint(
        "fk_user_domains_granted_by",
        "user_domains",
        type_="foreignkey",
    )
    op.drop_index("idx_user_domains_domain", table_name="user_domains", if_exists=True)
    _replace_user_domain_foreign_keys("CASCADE", "CASCADE")
    op.drop_column("user_domains", "granted_by")
    op.alter_column(
        "user_domains",
        "created_at",
        existing_type=sa.DateTime(timezone=True),
        existing_nullable=False,
        server_default=None,
    )

    for column_name in ("created_at",):
        op.alter_column(
            "domains",
            column_name,
            existing_type=sa.DateTime(timezone=True),
            existing_nullable=False,
            server_default=None,
        )
    for column_name in ("is_active", "is_default"):
        op.alter_column(
            "domains",
            column_name,
            existing_type=sa.Boolean(),
            existing_nullable=False,
            server_default=None,
        )
    op.drop_column("domains", "updated_at")
    op.drop_column("domains", "timezone")

    for column_name in ("created_at", "updated_at"):
        op.alter_column(
            "users",
            column_name,
            existing_type=sa.DateTime(timezone=True),
            existing_nullable=False,
            server_default=None,
        )
    op.alter_column(
        "users",
        "is_active",
        existing_type=sa.Boolean(),
        existing_nullable=False,
        server_default=None,
    )
