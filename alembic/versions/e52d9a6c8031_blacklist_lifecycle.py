"""add blacklist address and removal lifecycle controls

Revision ID: e52d9a6c8031
Revises: de4c81b75920
"""

import ipaddress
from collections import Counter

import sqlalchemy as sa
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "e52d9a6c8031"
down_revision = "de4c81b75920"
branch_labels = None
depends_on = None


def _validate_legacy_ips() -> None:
    """Reject unrepresentable or colliding addresses before any migration write."""
    rows = op.get_bind().execute(sa.text("SELECT ip FROM ip_blacklist")).scalars().all()
    canonical_ips: list[str] = []
    invalid_count = 0
    for value in rows:
        try:
            canonical_ips.append(str(ipaddress.ip_address(value)))
        except ValueError:
            invalid_count += 1
    if invalid_count:
        raise RuntimeError(f"ip blacklist IP invariant violated: {invalid_count}")
    duplicate_count = sum(count > 1 for count in Counter(canonical_ips).values())
    if duplicate_count:
        raise RuntimeError(
            f"ip blacklist canonical uniqueness invariant violated: {duplicate_count}"
        )


def _replace_created_by_foreign_key() -> None:
    for foreign_key in inspect(op.get_bind()).get_foreign_keys("ip_blacklist"):
        if foreign_key["constrained_columns"] != ["created_by"]:
            continue
        name = foreign_key["name"]
        if not isinstance(name, str) or not name:
            raise RuntimeError(
                "ip blacklist created actor foreign key has no inspectable name"
            )
        op.drop_constraint(name, "ip_blacklist", type_="foreignkey")
        break
    op.create_foreign_key(
        "fk_ip_blacklist_created_by",
        "ip_blacklist",
        "users",
        ["created_by"],
        ["id"],
        ondelete="RESTRICT",
    )


def _restore_parent_created_by_foreign_key() -> None:
    op.drop_constraint("fk_ip_blacklist_created_by", "ip_blacklist", type_="foreignkey")
    op.create_foreign_key(
        "ip_blacklist_created_by_fkey",
        "ip_blacklist",
        "users",
        ["created_by"],
        ["id"],
    )


def upgrade() -> None:
    _validate_legacy_ips()

    op.execute(
        """
        UPDATE ip_blacklist
        SET reason = 'Legacy blacklist entry'
        WHERE reason IS NULL OR btrim(reason) = ''
        """
    )
    op.alter_column(
        "ip_blacklist",
        "ip",
        existing_type=sa.String(length=64),
        type_=postgresql.INET(),
        postgresql_using="ip::inet",
    )
    op.alter_column("ip_blacklist", "reason", existing_type=sa.Text(), nullable=False)
    op.add_column(
        "ip_blacklist",
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "ip_blacklist",
        sa.Column("removed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "ip_blacklist",
        sa.Column("removed_by", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column("ip_blacklist", sa.Column("removal_reason", sa.Text(), nullable=True))
    _replace_created_by_foreign_key()
    op.create_foreign_key(
        "fk_ip_blacklist_removed_by",
        "ip_blacklist",
        "users",
        ["removed_by"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_check_constraint(
        "ck_ip_blacklist_reason", "ip_blacklist", "btrim(reason) <> ''"
    )
    op.create_check_constraint(
        "ck_ip_blacklist_removed_actor",
        "ip_blacklist",
        "removed_by IS NULL OR removed_at IS NOT NULL",
    )
    op.create_index(
        "idx_ip_blacklist_active_expires_at",
        "ip_blacklist",
        ["expires_at"],
        unique=False,
        postgresql_where=sa.text("removed_at IS NULL"),
    )
    op.alter_column(
        "ip_blacklist",
        "id",
        existing_type=postgresql.UUID(as_uuid=True),
        existing_nullable=False,
        server_default=sa.text("gen_random_uuid()"),
    )
    op.alter_column(
        "ip_blacklist",
        "created_at",
        existing_type=sa.DateTime(timezone=True),
        existing_nullable=False,
        server_default=sa.text("now()"),
    )


def downgrade() -> None:
    op.drop_index("idx_ip_blacklist_active_expires_at", table_name="ip_blacklist")
    op.drop_constraint("ck_ip_blacklist_removed_actor", "ip_blacklist", type_="check")
    op.drop_constraint("ck_ip_blacklist_reason", "ip_blacklist", type_="check")
    op.drop_constraint("fk_ip_blacklist_removed_by", "ip_blacklist", type_="foreignkey")
    _restore_parent_created_by_foreign_key()
    op.drop_column("ip_blacklist", "removal_reason")
    op.drop_column("ip_blacklist", "removed_by")
    op.drop_column("ip_blacklist", "removed_at")
    op.drop_column("ip_blacklist", "expires_at")
    op.alter_column(
        "ip_blacklist",
        "reason",
        existing_type=sa.Text(),
        nullable=True,
    )
    op.alter_column(
        "ip_blacklist",
        "ip",
        existing_type=postgresql.INET(),
        type_=sa.String(length=64),
        postgresql_using="host(ip)",
    )
    op.alter_column(
        "ip_blacklist",
        "created_at",
        existing_type=sa.DateTime(timezone=True),
        existing_nullable=False,
        server_default=None,
    )
