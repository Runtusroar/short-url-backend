import re
import uuid

import psycopg
import pytest
from psycopg import sql
from sqlalchemy.engine import make_url

from app.core.config import settings

DATABASE_PREFIX = "shorturl_migration_"
DATABASE_NAME_RE = re.compile(r"^shorturl_migration_[0-9a-f]{32}$")


def _validated_database_name(database_name: str) -> str:
    if DATABASE_NAME_RE.fullmatch(database_name) is None:
        raise ValueError("refusing to manage an invalid migration database name")
    return database_name


@pytest.fixture
def migration_database_url() -> str:
    configured_url = make_url(settings.database_url).set(
        drivername="postgresql+psycopg"
    )
    database_name = f"{DATABASE_PREFIX}{uuid.uuid4().hex}"
    assert DATABASE_NAME_RE.fullmatch(database_name)

    admin_url = configured_url.set(drivername="postgresql", database="postgres")
    disposable_url = configured_url.set(database=database_name)
    admin_dsn = admin_url.render_as_string(hide_password=False)

    with (
        psycopg.connect(admin_dsn, autocommit=True) as admin_connection,
        admin_connection.cursor() as cursor,
    ):
        cursor.execute(
            sql.SQL("CREATE DATABASE {}").format(
                sql.Identifier(_validated_database_name(database_name))
            )
        )

    try:
        yield disposable_url.render_as_string(hide_password=False)
    finally:
        validated_name = _validated_database_name(database_name)
        with (
            psycopg.connect(admin_dsn, autocommit=True) as admin_connection,
            admin_connection.cursor() as cursor,
        ):
            cursor.execute(
                """
                SELECT pg_terminate_backend(pid)
                FROM pg_stat_activity
                WHERE datname = %s AND pid <> pg_backend_pid()
                """,
                (validated_name,),
            )
            cursor.execute(
                sql.SQL("DROP DATABASE {} WITH (FORCE)").format(
                    sql.Identifier(_validated_database_name(validated_name))
                )
            )
