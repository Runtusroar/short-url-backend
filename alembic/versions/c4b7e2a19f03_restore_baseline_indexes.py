"""restore baseline indexes removed by 32736aed11b0

Revision ID: c4b7e2a19f03
Revises: a9e56b03bf5f
"""

from alembic import op


revision = "c4b7e2a19f03"
down_revision = "a9e56b03bf5f"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "idx_short_links_domain",
        "short_links",
        ["domain_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        "idx_access_logs_domain",
        "access_logs",
        ["domain_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        "idx_access_logs_short_link",
        "access_logs",
        ["short_link_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        "idx_access_logs_plus8",
        "access_logs",
        ["short_link_id", "accessed_at_plus8"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        "idx_access_logs_dedup",
        "access_logs",
        ["short_link_id", "ip", "dedup_bucket"],
        unique=False,
        if_not_exists=True,
    )


def downgrade() -> None:
    op.drop_index("idx_access_logs_dedup", table_name="access_logs", if_exists=True)
    op.drop_index("idx_access_logs_plus8", table_name="access_logs", if_exists=True)
    op.drop_index("idx_access_logs_short_link", table_name="access_logs", if_exists=True)
    op.drop_index("idx_access_logs_domain", table_name="access_logs", if_exists=True)
    op.drop_index("idx_short_links_domain", table_name="short_links", if_exists=True)
