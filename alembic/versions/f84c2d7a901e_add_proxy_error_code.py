"""add proxy error code

Revision ID: f84c2d7a901e
Revises: c8e4f1a26b73
"""

import sqlalchemy as sa

from alembic import op

revision = "f84c2d7a901e"
down_revision = "c8e4f1a26b73"
branch_labels = None
depends_on = None

PROXY_ERROR_CHECK = (
    "proxy_error_code IS NULL OR proxy_error_code = ANY "
    "(ARRAY['disabled', 'redis_unavailable', 'auth_failed', "
    "'insufficient_funds', 'permission_denied', 'rate_limited', 'timeout', "
    "'upstream_error', 'invalid_response', 'ip_not_found', 'invalid_ip', "
    "'non_global_ip', 'lookup_contended'])"
)


def upgrade() -> None:
    op.add_column(
        "access_logs", sa.Column("proxy_error_code", sa.String(32), nullable=True)
    )
    op.create_check_constraint(
        "ck_access_logs_proxy_error_code", "access_logs", PROXY_ERROR_CHECK
    )


def downgrade() -> None:
    connection = op.get_bind()
    connection.execute(
        sa.text("LOCK TABLE access_logs IN ACCESS EXCLUSIVE MODE")
    )
    count = connection.execute(
        sa.text("SELECT count(*) FROM access_logs WHERE proxy_error_code IS NOT NULL")
    ).scalar_one()
    if count:
        raise RuntimeError("proxy error downgrade invariant")
    op.drop_constraint(
        "ck_access_logs_proxy_error_code", "access_logs", type_="check"
    )
    op.drop_column("access_logs", "proxy_error_code")
