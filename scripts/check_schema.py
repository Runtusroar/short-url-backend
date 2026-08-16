import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

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
EXPECTED_INDEX_SORTING = {
    "idx_access_logs_domain_accessed_at": {"accessed_at": ("desc",)},
    "idx_access_logs_link_accessed_at": {"accessed_at": ("desc",)},
    "idx_access_logs_link_access_date": {"access_date": ("desc",)},
    "idx_access_logs_link_client_ip_dedup": {},
    "idx_access_logs_domain_result_accessed_at": {"accessed_at": ("desc",)},
    "idx_access_logs_domain_country_accessed_at": {"accessed_at": ("desc",)},
}
EXPECTED_ONDELETE = {
    "short_link_id": "RESTRICT",
    "domain_id": "RESTRICT",
    "target_url_id": "SET NULL",
    "matched_rule_id": "SET NULL",
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
    index_sorting: dict[str, dict[str, dict[str, tuple[str, ...]] | None]] | None = None,
    foreign_key_actions: dict[str, str | None] | None = None,
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
            # A same-named final index is no longer historical ambiguity: even
            # preflight must reject unknown or wrong ordering for that object.
            if mode in {"pre", "post"}:
                if index_sorting is None:
                    return _error(
                        mode=mode,
                        error_code="index_sorting_unknown",
                        detail=identifier,
                        schema="drifted",
                        indexes="invalid",
                        target_url_ondelete=normalized_ondelete,
                    )
                sorting = index_sorting.get(table_name, {}).get(index_name)
                if sorting != EXPECTED_INDEX_SORTING[index_name]:
                    return _error(
                        mode=mode,
                        error_code="index_sorting_mismatch",
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

    # A partial final-index set can still be repaired by the Task5 migration.
    # Once every final index exists, its four explicit access-log FK actions
    # are final schema facts and must be complete and exact in preflight too.
    has_complete_final_indexes = not missing_indexes
    if mode == "post" or has_complete_final_indexes:
        if foreign_key_actions is None:
            return _error(
                mode=mode,
                error_code="access_log_foreign_key_unknown",
                detail="access_logs",
                schema="drifted",
                indexes="missing" if missing_indexes else "valid",
                target_url_ondelete=normalized_ondelete,
            )
        for column, expected in EXPECTED_ONDELETE.items():
            if foreign_key_actions.get(column) != expected:
                return _error(
                    mode=mode,
                    error_code="access_log_foreign_key_mismatch",
                    detail=f"access_logs.{column}",
                    schema="drifted",
                    indexes="missing" if missing_indexes else "valid",
                    target_url_ondelete=normalized_ondelete,
                )

        if normalized_ondelete != "SET NULL":
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
    dict[str, dict[str, dict[str, tuple[str, ...]] | None]],
    dict[str, str | None],
]:
    engine = create_engine(database_url)
    try:
        inspector = inspect(engine)
        tables = set(inspector.get_table_names())
        indexes: dict[str, dict[str, tuple[str, ...]]] = {}
        uniqueness: dict[str, dict[str, bool | None]] = {}
        sorting: dict[str, dict[str, dict[str, tuple[str, ...]] | None]] = {}

        for table_name, expected_by_name in EXPECTED_INDEXES.items():
            indexes[table_name] = {}
            uniqueness[table_name] = {}
            sorting[table_name] = {}
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
                raw_sorting = index.get("column_sorting")
                sorting[table_name][index_name] = (
                    {key: tuple(value) for key, value in raw_sorting.items()}
                    if isinstance(raw_sorting, dict)
                    else {}
                )

        foreign_key_actions: dict[str, str | None] = {}
        if "access_logs" in tables:
            for foreign_key in inspector.get_foreign_keys("access_logs"):
                columns = foreign_key.get("constrained_columns")
                if not isinstance(columns, list) or len(columns) != 1:
                    continue
                ondelete = foreign_key.get("options", {}).get("ondelete")
                foreign_key_actions[columns[0]] = ondelete.upper() if ondelete else None

        return tables, indexes, uniqueness, sorting, foreign_key_actions
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

        tables, indexes, uniqueness, sorting, foreign_key_actions = inspect_schema(settings.database_url)
        result = evaluate_schema(
            mode=parsed.mode,
            tables=tables,
            indexes=indexes,
            target_url_ondelete=foreign_key_actions.get("target_url_id"),
            index_uniqueness=uniqueness,
            index_sorting=sorting,
            foreign_key_actions=foreign_key_actions,
        )
    except Exception:  # noqa: BLE001
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
