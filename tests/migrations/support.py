import os
import re
import subprocess
import sys
import uuid
from contextlib import contextmanager
from pathlib import Path

import psycopg
from psycopg import sql
from sqlalchemy import create_engine, inspect
from sqlalchemy.engine import make_url

from app.core.config import settings

ROOT = Path(__file__).parents[2]
DATABASE_PREFIX = "shorturl_migration_"
DATABASE_NAME_RE = re.compile(r"^shorturl_migration_[0-9a-f]{32}$")

BASELINE_INDEX_NAMES = {
    "short_links": set(),
    "access_logs": {
        "idx_access_logs_domain_accessed_at",
        "idx_access_logs_link_accessed_at",
        "idx_access_logs_link_access_date",
        "idx_access_logs_link_client_ip_dedup",
        "idx_access_logs_domain_result_accessed_at",
        "idx_access_logs_domain_country_accessed_at",
    },
}


def _validated_database_name(database_name: str) -> str:
    if DATABASE_NAME_RE.fullmatch(database_name) is None:
        raise ValueError("refusing to manage an invalid migration database name")
    return database_name


@contextmanager
def disposable_migration_database():
    """Yield a validated disposable PostgreSQL database URL for migration tests."""
    configured_url = make_url(settings.database_url).set(drivername="postgresql+psycopg")
    database_name = f"{DATABASE_PREFIX}{uuid.uuid4().hex}"
    _validated_database_name(database_name)
    admin_url = configured_url.set(drivername="postgresql", database="postgres")
    disposable_url = configured_url.set(database=database_name)
    admin_dsn = admin_url.render_as_string(hide_password=False)

    with psycopg.connect(admin_dsn, autocommit=True) as admin_connection, admin_connection.cursor() as cursor:
        cursor.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database_name)))

    try:
        yield disposable_url.render_as_string(hide_password=False)
    finally:
        validated_name = _validated_database_name(database_name)
        with psycopg.connect(admin_dsn, autocommit=True) as admin_connection, admin_connection.cursor() as cursor:
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
                    sql.Identifier(validated_name)
                )
            )


def run_alembic(
    database_url: str, *arguments: str
) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["DATABASE_URL"] = database_url
    return subprocess.run(
        [sys.executable, "-m", "alembic", *arguments],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=True,
    )


def get_indexes(database_url: str, table_name: str) -> dict[str, tuple[str, ...]]:
    expected_names = BASELINE_INDEX_NAMES.get(table_name, set())
    engine = create_engine(database_url)
    try:
        return {
            index["name"]: tuple(index["column_names"])
            for index in inspect(engine).get_indexes(table_name)
            if index["name"] in expected_names
        }
    finally:
        engine.dispose()


def get_foreign_keys(
    database_url: str, table_name: str
) -> dict[str, dict[str, str | tuple[str, ...] | None]]:
    engine = create_engine(database_url)
    try:
        foreign_keys = inspect(engine).get_foreign_keys(table_name)
        return {
            foreign_key["constrained_columns"][0]: {
                "name": foreign_key["name"],
                "referred_table": foreign_key["referred_table"],
                "constrained_columns": tuple(foreign_key["constrained_columns"]),
                "ondelete": (
                    foreign_key.get("options", {}).get("ondelete") or ""
                ).upper()
                or None,
            }
            for foreign_key in foreign_keys
            if foreign_key["constrained_columns"]
        }
    finally:
        engine.dispose()


def target_url_ondelete(database_url: str) -> str | None:
    target_url_foreign_key = get_foreign_keys(database_url, "access_logs").get(
        "target_url_id"
    )
    if target_url_foreign_key is None:
        return None
    ondelete = target_url_foreign_key["ondelete"]
    return ondelete if isinstance(ondelete, str) else None
