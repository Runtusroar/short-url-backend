"""init

Revision ID: 20240815_init
Revises:
Create Date: 2024-08-15 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "20240815_init"
down_revision = None
branch_labels = None
depends_on = None

_uuid_pk = lambda: sa.Column(
    "id",
    postgresql.UUID(as_uuid=True),
    primary_key=True,
    server_default=sa.text("gen_random_uuid()"),
)


def upgrade() -> None:
    op.create_table(
        "users",
        _uuid_pk(),
        sa.Column("username", sa.String(64), unique=True, nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("is_active", sa.Boolean(), default=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "short_links",
        _uuid_pk(),
        sa.Column("short_code", sa.String(32), unique=True, nullable=False),
        sa.Column("is_custom_alias", sa.Boolean(), default=False, nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "owner_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False
        ),
        sa.Column("is_active", sa.Boolean(), default=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "short_link_permissions",
        _uuid_pk(),
        sa.Column(
            "short_link_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("short_links.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("short_link_id", "user_id", name="uq_short_link_user"),
    )

    op.create_table(
        "target_urls",
        _uuid_pk(),
        sa.Column(
            "short_link_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("short_links.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("url_type", sa.String(16), nullable=False),
        sa.Column("weight", sa.Integer(), default=1, nullable=False),
        sa.Column("is_active", sa.Boolean(), default=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "access_rules",
        _uuid_pk(),
        sa.Column(
            "short_link_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("short_links.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("action", sa.String(16), nullable=False),
        sa.Column("priority", sa.Integer(), default=0, nullable=False),
        sa.Column("countries", postgresql.JSONB(), default=list),
        sa.Column("ua_platforms", postgresql.JSONB(), default=list),
        sa.Column("referer_pattern", sa.String(255), nullable=True),
        sa.Column("is_active", sa.Boolean(), default=True, nullable=False),
    )

    op.create_table(
        "ip_blacklist",
        _uuid_pk(),
        sa.Column("ip", sa.String(64), unique=True, nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column(
            "created_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "access_logs",
        _uuid_pk(),
        sa.Column(
            "short_link_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("short_links.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "target_url_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("target_urls.id"),
            nullable=True,
        ),
        sa.Column("result", sa.String(16), nullable=False),
        sa.Column("ip", sa.String(64), nullable=False),
        sa.Column("country", sa.String(8), nullable=True),
        sa.Column("ua_string", sa.Text(), nullable=True),
        sa.Column("ua_platform", sa.String(64), nullable=True),
        sa.Column("referer", sa.Text(), nullable=True),
        sa.Column("accessed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accessed_at_plus8", sa.Date(), nullable=False),
        sa.Column("dedup_bucket", sa.BigInteger(), nullable=False),
    )

    op.create_index("idx_access_logs_short_link", "access_logs", ["short_link_id"])
    op.create_index("idx_access_logs_dedup", "access_logs", ["short_link_id", "ip", "dedup_bucket"])
    op.create_index("idx_access_logs_plus8", "access_logs", ["short_link_id", "accessed_at_plus8"])


def downgrade() -> None:
    op.drop_index("idx_access_logs_plus8", table_name="access_logs")
    op.drop_index("idx_access_logs_dedup", table_name="access_logs")
    op.drop_index("idx_access_logs_short_link", table_name="access_logs")
    op.drop_table("access_logs")
    op.drop_table("ip_blacklist")
    op.drop_table("access_rules")
    op.drop_table("target_urls")
    op.drop_table("short_link_permissions")
    op.drop_table("short_links")
    op.drop_table("users")
