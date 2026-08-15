"""repair target URL foreign-key drift

Revision ID: d6e8f0a21b35
Revises: c4b7e2a19f03
"""

from sqlalchemy import inspect

from alembic import op

revision = "d6e8f0a21b35"
down_revision = "c4b7e2a19f03"
branch_labels = None
depends_on = None


def _replace_target_url_foreign_key(constraint_name: str) -> None:
    foreign_keys = inspect(op.get_bind()).get_foreign_keys("access_logs")
    target_url_foreign_keys = [
        foreign_key
        for foreign_key in foreign_keys
        if foreign_key["constrained_columns"] == ["target_url_id"]
    ]

    for foreign_key in target_url_foreign_keys:
        inspected_name = foreign_key["name"]
        if not isinstance(inspected_name, str) or not inspected_name:
            raise RuntimeError("target_url_id foreign key has no inspectable name")
        op.drop_constraint(
            inspected_name,
            "access_logs",
            type_="foreignkey",
        )

    op.create_foreign_key(
        constraint_name,
        "access_logs",
        "target_urls",
        ["target_url_id"],
        ["id"],
        ondelete="SET NULL",
    )


def upgrade() -> None:
    _replace_target_url_foreign_key("fk_access_logs_target_url")


def downgrade() -> None:
    # Restore the name expected by a9e56b03bf5f's historical downgrade while
    # retaining c4b7e2a19f03's canonical SET NULL contract.
    _replace_target_url_foreign_key("access_logs_target_url_id_fkey")
