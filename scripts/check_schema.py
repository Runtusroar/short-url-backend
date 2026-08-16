import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

from sqlalchemy import create_engine, inspect


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

EXPECTED_INDEXES = {
    "access_logs": {
        "idx_access_logs_domain_accessed_at": ("domain_id", "accessed_at"),
        "idx_access_logs_link_accessed_at": ("short_link_id", "accessed_at"),
        "idx_access_logs_link_access_date": ("short_link_id", "access_date"),
        "idx_access_logs_link_client_ip_dedup": ("short_link_id", "client_ip", "dedup_bucket"),
        "idx_access_logs_domain_result_accessed_at": ("domain_id", "result", "accessed_at"),
        "idx_access_logs_domain_country_accessed_at": ("domain_id", "country", "accessed_at"),
    },
}
TARGET_TABLES = set(EXPECTED_INDEXES)
CURRENT_APPLICATION_TABLES = {
    "users",
    "domains",
    "user_domains",
    "ip_blacklist",
    "short_links",
    "short_link_permissions",
    "target_urls",
    "access_rules",
    "access_logs",
}


def _error(
    *,
    mode: str,
    error_code: str,
    detail: str,
    schema: str,
    indexes: str,
    target_url_ondelete: str | None,
) -> dict[str, object]:
    return {
        "mode": mode,
        "status": "error",
        "error_code": error_code,
        "detail": detail,
        "schema": schema,
        "indexes": indexes,
        "target_url_ondelete": target_url_ondelete,
    }


def evaluate_schema(
    *,
    mode: str,
    tables: set[str],
    indexes: dict[str, dict[str, tuple[str, ...]]],
    target_url_ondelete: str | None,
    index_uniqueness: dict[str, dict[str, bool | None]] | None = None,
) -> dict[str, object]:
    """Evaluate inspected schema facts without opening a database connection.

    ``index_uniqueness`` is a side map so callers using the documented
    column-only ``indexes`` interface remain compatible. Database inspection
    always supplies the side map, allowing conflicting unique indexes to be
    rejected without changing the index-column representation.
    """
    normalized_ondelete = (
        target_url_ondelete.upper() if target_url_ondelete is not None else None
    )
    present_application_tables = tables & CURRENT_APPLICATION_TABLES

    if mode not in {"pre", "post"}:
        return _error(
            mode=mode,
            error_code="invalid_mode",
            detail="mode",
            schema="unknown",
            indexes="unknown",
            target_url_ondelete=normalized_ondelete,
        )

    if not present_application_tables:
        if mode == "pre":
            return {
                "mode": mode,
                "status": "ok",
                "schema": "empty",
                "indexes": "not_applicable",
                "target_url_ondelete": None,
            }
        return _error(
            mode=mode,
            error_code="schema_empty",
            detail="access_logs,short_links",
            schema="empty",
            indexes="missing",
            target_url_ondelete=None,
        )

    missing_tables = sorted(TARGET_TABLES - tables)
    if missing_tables:
        return _error(
            mode=mode,
            error_code="schema_incomplete",
            detail=",".join(missing_tables),
            schema="incomplete",
            indexes="unknown",
            target_url_ondelete=normalized_ondelete,
        )

    missing_indexes: list[str] = []
    uniqueness = index_uniqueness or {}
    for table_name, expected_by_name in EXPECTED_INDEXES.items():
        actual_by_name = indexes.get(table_name, {})
        unique_by_name = uniqueness.get(table_name, {})
        for index_name, expected_columns in expected_by_name.items():
            actual_columns = actual_by_name.get(index_name)
            identifier = f"{table_name}.{index_name}"
            if actual_columns is None:
                missing_indexes.append(identifier)
                continue
            if actual_columns != expected_columns:
                return _error(
                    mode=mode,
                    error_code="index_definition_mismatch",
                    detail=identifier,
                    schema="drifted",
                    indexes="invalid",
                    target_url_ondelete=normalized_ondelete,
                )
            unique = unique_by_name.get(index_name)
            if not isinstance(unique, bool):
                return _error(
                    mode=mode,
                    error_code="index_uniqueness_unknown",
                    detail=identifier,
                    schema="drifted",
                    indexes="invalid",
                    target_url_ondelete=normalized_ondelete,
                )
            if unique:
                return _error(
                    mode=mode,
                    error_code="index_uniqueness_mismatch",
                    detail=identifier,
                    schema="drifted",
                    indexes="invalid",
                    target_url_ondelete=normalized_ondelete,
                )

    if mode == "post" and missing_indexes:
        return _error(
            mode=mode,
            error_code="required_index_missing",
            detail=",".join(missing_indexes),
            schema="incomplete",
            indexes="missing",
            target_url_ondelete=normalized_ondelete,
        )

    if mode == "post" and normalized_ondelete != "SET NULL":
        return _error(
            mode=mode,
            error_code="target_url_foreign_key_mismatch",
            detail="access_logs.target_url_id",
            schema="drifted",
            indexes="missing" if missing_indexes else "valid",
            target_url_ondelete=normalized_ondelete,
        )

    if missing_indexes or normalized_ondelete != "SET NULL":
        return {
            "mode": mode,
            "status": "ok",
            "schema": "repairable",
            "indexes": "missing" if missing_indexes else "valid",
            "target_url_ondelete": normalized_ondelete,
        }

    return {
        "mode": mode,
        "status": "ok",
        "schema": "ready",
        "indexes": "valid",
        "target_url_ondelete": normalized_ondelete,
    }


def inspect_schema(
    database_url: str,
) -> tuple[
    set[str],
    dict[str, dict[str, tuple[str, ...]]],
    dict[str, dict[str, bool | None]],
    str | None,
]:
    engine = create_engine(database_url)
    try:
        inspector = inspect(engine)
        tables = set(inspector.get_table_names())
        indexes: dict[str, dict[str, tuple[str, ...]]] = {}
        uniqueness: dict[str, dict[str, bool | None]] = {}

        for table_name, expected_by_name in EXPECTED_INDEXES.items():
            indexes[table_name] = {}
            uniqueness[table_name] = {}
            if table_name not in tables:
                continue
            for index in inspector.get_indexes(table_name):
                index_name = index.get("name")
                if index_name not in expected_by_name:
                    continue
                indexes[table_name][index_name] = tuple(
                    index.get("column_names") or ()
                )
                unique = index.get("unique")
                uniqueness[table_name][index_name] = (
                    unique if isinstance(unique, bool) else None
                )

        target_url_ondelete = None
        if "access_logs" in tables:
            for foreign_key in inspector.get_foreign_keys("access_logs"):
                if foreign_key.get("constrained_columns") != ["target_url_id"]:
                    continue
                ondelete = foreign_key.get("options", {}).get("ondelete")
                target_url_ondelete = ondelete.upper() if ondelete else None
                break

        return tables, indexes, uniqueness, target_url_ondelete
    finally:
        engine.dispose()


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise ValueError("invalid schema-check arguments")


def main(arguments: Sequence[str] | None = None) -> int:
    parser = _ArgumentParser(add_help=False)
    parser.add_argument("--mode", choices=("pre", "post"), required=True)
    try:
        parsed = parser.parse_args(arguments)
    except (argparse.ArgumentError, ValueError):
        result: dict[str, object] = {
            "mode": None,
            "status": "error",
            "error_code": "invalid_arguments",
            "detail": "mode",
        }
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 2

    try:
        from app.core.config import settings

        tables, indexes, uniqueness, ondelete = inspect_schema(settings.database_url)
        result = evaluate_schema(
            mode=parsed.mode,
            tables=tables,
            indexes=indexes,
            target_url_ondelete=ondelete,
            index_uniqueness=uniqueness,
        )
    except Exception:
        result = {
            "mode": parsed.mode,
            "status": "error",
            "error_code": "schema_inspection_failed",
            "detail": "database_schema",
        }

    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
