"""multi_tenant

Revision ID: 20240816_multi_tenant
Revises: 20240815_init
Create Date: 2024-08-16 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "20240816_multi_tenant"
down_revision = "20240815_init"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Create domains table
    op.create_table(
        "domains",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("name", sa.String(255), unique=True, nullable=False),
        sa.Column("is_active", sa.Boolean(), default=True, nullable=False),
        sa.Column("is_default", sa.Boolean(), default=False, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    # 2. Create user_domains table
    op.create_table(
        "user_domains",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("domain_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("domains.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("user_id", "domain_id", name="uq_user_domain"),
    )

    # 3. Insert default domain
    op.execute(
        "INSERT INTO domains (id, name, is_active, is_default, created_at) VALUES (gen_random_uuid(), 'localhost', true, true, now())"
    )

    # 4. Add domain_id columns as nullable first
    op.add_column("short_links", sa.Column("domain_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("access_logs", sa.Column("domain_id", postgresql.UUID(as_uuid=True), nullable=True))

    # 5. Update existing rows to default domain
    op.execute(
        "UPDATE short_links SET domain_id = (SELECT id FROM domains WHERE is_default = true LIMIT 1)"
    )
    op.execute(
        "UPDATE access_logs SET domain_id = (SELECT id FROM domains WHERE is_default = true LIMIT 1)"
    )

    # 6. Make domain_id non-nullable
    op.alter_column("short_links", "domain_id", nullable=False)
    op.alter_column("access_logs", "domain_id", nullable=False)

    # 7. Add foreign keys
    op.create_foreign_key("fk_short_links_domain", "short_links", "domains", ["domain_id"], ["id"])
    op.create_foreign_key("fk_access_logs_domain", "access_logs", "domains", ["domain_id"], ["id"])

    # 8. Drop old unique constraint on short_code and create new composite unique
    op.drop_constraint("short_links_short_code_key", "short_links", type_="unique")
    op.create_unique_constraint("uq_domain_short_code", "short_links", ["domain_id", "short_code"])

    # 9. Add indexes
    op.create_index("idx_short_links_domain", "short_links", ["domain_id"])
    op.create_index("idx_access_logs_domain", "access_logs", ["domain_id"])


def downgrade() -> None:
    op.drop_index("idx_access_logs_domain", table_name="access_logs")
    op.drop_index("idx_short_links_domain", table_name="short_links")
    op.drop_constraint("uq_domain_short_code", "short_links", type_="unique")
    op.create_unique_constraint("short_links_short_code_key", "short_links", ["short_code"])
    op.drop_constraint("fk_access_logs_domain", "access_logs", type_="foreignkey")
    op.drop_constraint("fk_short_links_domain", "short_links", type_="foreignkey")
    op.drop_column("access_logs", "domain_id")
    op.drop_column("short_links", "domain_id")
    op.drop_table("user_domains")
    op.drop_table("domains")
