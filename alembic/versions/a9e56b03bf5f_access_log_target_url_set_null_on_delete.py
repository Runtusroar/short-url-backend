"""access_log_target_url_set_null_on_delete

Revision ID: a9e56b03bf5f
Revises: b1e5f6851085
Create Date: 2026-08-15 08:39:14.196646

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a9e56b03bf5f'
down_revision = 'b1e5f6851085'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint('access_logs_target_url_id_fkey', 'access_logs', type_='foreignkey')
    op.create_foreign_key(
        'access_logs_target_url_id_fkey',
        'access_logs',
        'target_urls',
        ['target_url_id'],
        ['id'],
        ondelete='SET NULL',
    )


def downgrade() -> None:
    op.drop_constraint('access_logs_target_url_id_fkey', 'access_logs', type_='foreignkey')
    op.create_foreign_key(
        'access_logs_target_url_id_fkey',
        'access_logs',
        'target_urls',
        ['target_url_id'],
        ['id'],
    )
