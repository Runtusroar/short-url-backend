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


def upgrade() -> None:
    foreign_keys = inspect(op.get_bind()).get_foreign_keys("access_logs")
    target_url_foreign_keys = [
        foreign_key
        for foreign_key in foreign_keys
        if foreign_key["constrained_columns"] == ["target_url_id"]
    ]

    for foreign_key in target_url_foreign_keys:
        constraint_name = foreign_key["name"]
        if not isinstance(constraint_name, str) or not constraint_name:
            raise RuntimeError("target_url_id foreign key has no inspectable name")
        op.drop_constraint(
            constraint_name,
            "access_logs",
            type_="foreignkey",
        )

    op.create_foreign_key(
        "fk_access_logs_target_url",
        "access_logs",
        "target_urls",
        ["target_url_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    # c4b7e2a19f03's canonical contract already requires SET NULL. The prior
    # drift is unknown, so downgrading must not invent or restore it.
    pass
