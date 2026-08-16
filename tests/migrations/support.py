import os
import re
import subprocess
import sys
import uuid
from contextlib import contextmanager
from pathlib import Path

import psycopg
from psycopg import sql
from sqlalchemy import create_engine, inspect, text
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
    configured_url = make_url(settings.database_url).set(
        drivername="postgresql+psycopg"
    )
    database_name = f"{DATABASE_PREFIX}{uuid.uuid4().hex}"
    _validated_database_name(database_name)
    admin_url = configured_url.set(drivername="postgresql", database="postgres")
    disposable_url = configured_url.set(database=database_name)
    admin_dsn = admin_url.render_as_string(hide_password=False)

    with (
        psycopg.connect(admin_dsn, autocommit=True) as admin_connection,
        admin_connection.cursor() as cursor,
    ):
        cursor.execute(
            sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database_name))
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
                    sql.Identifier(validated_name)
                )
            )


def run_alembic(database_url: str, *arguments: str) -> subprocess.CompletedProcess[str]:
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


def _normalized_predicate(value: object) -> str | None:
    """Return comparable PostgreSQL partial-index text without formatting noise."""
    if not isinstance(value, str):
        return None
    return " ".join(value.strip().removeprefix("(").removesuffix(")").split())


def get_schema_contract(database_url: str) -> dict[str, object]:
    """Inspect all final-schema facts used by migration-path contract tests.

    Unlike ``get_indexes``, this deliberately does not filter index names: a
    final-schema test needs to detect both missing required objects and stale
    transitional objects.
    """
    engine = create_engine(database_url)
    try:
        inspector = inspect(engine)
        tables = set(inspector.get_table_names())
        indexes: dict[str, dict[str, dict[str, object]]] = {}
        columns: dict[str, dict[str, dict[str, object]]] = {}
        checks: dict[str, dict[str, str]] = {}
        foreign_keys: dict[str, dict[str, dict[str, object]]] = {}

        with engine.connect() as connection:
            extension_names = set(
                connection.scalars(text("SELECT extname FROM pg_extension"))
            )

        for table_name in tables:
            with engine.connect() as connection:
                catalog_indexes = {
                    row["name"]: row
                    for row in connection.execute(
                    text(
                        """
                        SELECT index_class.relname AS name,
                               access_method.amname AS using,
                               pg_get_expr(index_data.indexprs, index_data.indrelid) AS expressions,
                               array_agg(
                                   CASE WHEN operator_class.opcdefault THEN ''
                                        ELSE operator_class.opcname END
                                   ORDER BY ordinality
                               ) AS operator_classes
                        FROM pg_index AS index_data
                        JOIN pg_class AS table_class
                          ON table_class.oid = index_data.indrelid
                        JOIN pg_namespace AS namespace
                          ON namespace.oid = table_class.relnamespace
                        JOIN pg_class AS index_class
                          ON index_class.oid = index_data.indexrelid
                        JOIN pg_am AS access_method
                          ON access_method.oid = index_class.relam
                        JOIN LATERAL unnest(index_data.indclass)
                          WITH ORDINALITY AS class_ids(operator_class_id, ordinality)
                          ON true
                        JOIN pg_opclass AS operator_class
                          ON operator_class.oid = class_ids.operator_class_id
                        WHERE namespace.nspname = current_schema()
                          AND table_class.relname = :table_name
                        GROUP BY index_class.relname, access_method.amname,
                                 index_data.indexprs, index_data.indrelid
                        """
                    ),
                    {"table_name": table_name},
                    ).mappings()
                }
            indexes[table_name] = {}
            for index in inspector.get_indexes(table_name):
                name = index.get("name")
                if not isinstance(name, str):
                    continue
                raw_sorting = index.get("column_sorting")
                sorting = (
                    {column: tuple(values) for column, values in raw_sorting.items()}
                    if isinstance(raw_sorting, dict)
                    else {}
                )
                dialect_options = index.get("dialect_options")
                predicate = (
                    _normalized_predicate(dialect_options.get("postgresql_where"))
                    if isinstance(dialect_options, dict)
                    else None
                )
                indexes[table_name][name] = {
                    "columns": tuple(index.get("column_names") or ()),
                    "unique": index.get("unique")
                    if isinstance(index.get("unique"), bool)
                    else None,
                    "sorting": sorting,
                    "predicate": predicate,
                    "using": catalog_indexes.get(name, {}).get("using"),
                    "expressions": (
                        (catalog_indexes[name]["expressions"],)
                        if catalog_indexes.get(name, {}).get("expressions")
                        else ()
                    ),
                    "operator_classes": tuple(
                        catalog_indexes.get(name, {}).get("operator_classes") or ()
                    ),
                }

            columns[table_name] = {
                column["name"]: {
                    "type": str(column["type"]),
                    "timezone": getattr(column["type"], "timezone", None),
                    "nullable": column["nullable"],
                    "default": column.get("default"),
                }
                for column in inspector.get_columns(table_name)
            }
            checks[table_name] = {
                check["name"]: check["sqltext"]
                for check in inspector.get_check_constraints(table_name)
                if isinstance(check.get("name"), str)
                and isinstance(check.get("sqltext"), str)
            }
            foreign_keys[table_name] = {
                foreign_key["name"]: {
                    "columns": tuple(foreign_key.get("constrained_columns") or ()),
                    "referred_table": foreign_key.get("referred_table"),
                    "referred_columns": tuple(
                        foreign_key.get("referred_columns") or ()
                    ),
                    "ondelete": (
                        foreign_key.get("options", {}).get("ondelete") or ""
                    ).upper()
                    or None,
                }
                for foreign_key in inspector.get_foreign_keys(table_name)
                if isinstance(foreign_key.get("name"), str)
            }

        return {
            "tables": tables,
            "indexes": indexes,
            "columns": columns,
            "checks": checks,
            "foreign_keys": foreign_keys,
            "extensions": extension_names,
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
