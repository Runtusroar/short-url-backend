import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from sqlalchemy import create_engine, inspect

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Each definition is a final Phase 4 object, not a subset chosen for a
# particular query.  Preflight permits an object to be absent because the
# migration chain can create it; a same-named object with different facts is
# drift and must never be guessed at or overwritten.
EXPECTED_INDEXES = {
    "domains": {
        "uq_domains_one_default": {
            "columns": ("is_default",),
            "unique": True,
            "sorting": {},
            "predicate": "is_default",
        },
    },
    "user_domains": {
        "idx_user_domains_domain": {
            "columns": ("domain_id",),
            "unique": False,
            "sorting": {},
            "predicate": None,
        },
    },
    "short_links": {
        "idx_short_links_domain_created_at": {
            "columns": ("domain_id", "created_at"),
            "unique": False,
            "sorting": {"created_at": ("desc",)},
            "predicate": None,
        },
        "idx_short_links_domain_owner_created_at": {
            "columns": ("domain_id", "owner_id", "created_at"),
            "unique": False,
            "sorting": {"created_at": ("desc",)},
            "predicate": None,
        },
    },
    "access_rules": {
        "idx_access_rules_link_active_priority": {
            "columns": ("short_link_id", "priority", "id"),
            "unique": False,
            "sorting": {},
            "predicate": "is_active",
        },
    },
    "ip_blacklist": {
        "idx_ip_blacklist_active_expires_at": {
            "columns": ("expires_at",),
            "unique": False,
            "sorting": {},
            "predicate": "removed_at IS NULL",
        },
    },
    "access_logs": {
        "idx_access_logs_domain_accessed_at": {
            "columns": ("domain_id", "accessed_at"),
            "unique": False,
            "sorting": {"accessed_at": ("desc",)},
            "predicate": None,
        },
        "idx_access_logs_link_accessed_at": {
            "columns": ("short_link_id", "accessed_at"),
            "unique": False,
            "sorting": {"accessed_at": ("desc",)},
            "predicate": None,
        },
        "idx_access_logs_link_access_date": {
            "columns": ("short_link_id", "access_date"),
            "unique": False,
            "sorting": {"access_date": ("desc",)},
            "predicate": None,
        },
        "idx_access_logs_link_client_ip_dedup": {
            "columns": ("short_link_id", "client_ip", "dedup_bucket"),
            "unique": False,
            "sorting": {},
            "predicate": None,
        },
        "idx_access_logs_domain_result_accessed_at": {
            "columns": ("domain_id", "result", "accessed_at"),
            "unique": False,
            "sorting": {"accessed_at": ("desc",)},
            "predicate": None,
        },
        "idx_access_logs_domain_country_accessed_at": {
            "columns": ("domain_id", "country", "accessed_at"),
            "unique": False,
            "sorting": {"accessed_at": ("desc",)},
            "predicate": None,
        },
    },
}
EXPECTED_INDEX_SORTING = {
    name: definition["sorting"]
    for definitions in EXPECTED_INDEXES.values()
    for name, definition in definitions.items()
}
EXPECTED_ONDELETE = {
    "short_link_id": "RESTRICT",
    "domain_id": "RESTRICT",
    "target_url_id": "SET NULL",
    "matched_rule_id": "SET NULL",
}
EXPECTED_ACCESS_LOG_FOREIGN_KEYS = {
    "fk_access_logs_short_link": ("short_link_id", "short_links", "RESTRICT"),
    "fk_access_logs_domain": ("domain_id", "domains", "RESTRICT"),
    "fk_access_logs_target_url": ("target_url_id", "target_urls", "SET NULL"),
    "fk_access_logs_matched_rule": ("matched_rule_id", "access_rules", "SET NULL"),
}
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


def _normalize_predicate(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    return " ".join(value.strip().removeprefix("(").removesuffix(")").split())


def evaluate_schema(
    *,
    mode: str,
    tables: set[str],
    indexes: dict[str, dict[str, tuple[str, ...]]],
    target_url_ondelete: str | None,
    index_uniqueness: dict[str, dict[str, bool | None]] | None = None,
    index_sorting: dict[str, dict[str, dict[str, tuple[str, ...]] | None]]
    | None = None,
    index_predicates: dict[str, dict[str, str | None]] | None = None,
    foreign_key_actions: dict[str, str | None] | None = None,
    foreign_keys_by_name: dict[str, tuple[str, str, str | None]] | None = None,
) -> dict[str, object]:
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
            detail="application_tables",
            schema="empty",
            indexes="missing",
            target_url_ondelete=None,
        )
    missing_tables = sorted(CURRENT_APPLICATION_TABLES - tables)
    if missing_tables:
        return _error(
            mode=mode,
            error_code="schema_incomplete",
            detail=",".join(missing_tables),
            schema="incomplete",
            indexes="unknown",
            target_url_ondelete=normalized_ondelete,
        )

    uniqueness = index_uniqueness or {}
    predicates = index_predicates or {}
    missing_indexes: list[str] = []
    for table_name, definitions in EXPECTED_INDEXES.items():
        for index_name, expected in definitions.items():
            identifier = f"{table_name}.{index_name}"
            actual_columns = indexes.get(table_name, {}).get(index_name)
            if actual_columns is None:
                missing_indexes.append(identifier)
                continue
            if actual_columns != expected["columns"]:
                return _error(
                    mode=mode,
                    error_code="index_definition_mismatch",
                    detail=identifier,
                    schema="drifted",
                    indexes="invalid",
                    target_url_ondelete=normalized_ondelete,
                )
            actual_unique = uniqueness.get(table_name, {}).get(index_name)
            if not isinstance(actual_unique, bool):
                return _error(
                    mode=mode,
                    error_code="index_uniqueness_unknown",
                    detail=identifier,
                    schema="drifted",
                    indexes="invalid",
                    target_url_ondelete=normalized_ondelete,
                )
            if actual_unique != expected["unique"]:
                return _error(
                    mode=mode,
                    error_code="index_uniqueness_mismatch",
                    detail=identifier,
                    schema="drifted",
                    indexes="invalid",
                    target_url_ondelete=normalized_ondelete,
                )
            if index_sorting is None:
                return _error(
                    mode=mode,
                    error_code="index_sorting_unknown",
                    detail=identifier,
                    schema="drifted",
                    indexes="invalid",
                    target_url_ondelete=normalized_ondelete,
                )
            if index_sorting.get(table_name, {}).get(index_name) != expected["sorting"]:
                return _error(
                    mode=mode,
                    error_code="index_sorting_mismatch",
                    detail=identifier,
                    schema="drifted",
                    indexes="invalid",
                    target_url_ondelete=normalized_ondelete,
                )
            if index_predicates is None:
                return _error(
                    mode=mode,
                    error_code="index_predicate_unknown",
                    detail=identifier,
                    schema="drifted",
                    indexes="invalid",
                    target_url_ondelete=normalized_ondelete,
                )
            if (
                _normalize_predicate(predicates.get(table_name, {}).get(index_name))
                != expected["predicate"]
            ):
                return _error(
                    mode=mode,
                    error_code="index_predicate_mismatch",
                    detail=identifier,
                    schema="drifted",
                    indexes="invalid",
                    target_url_ondelete=normalized_ondelete,
                )

    named_foreign_keys = foreign_keys_by_name or {}
    # D6 already happens to use two final FK *names* with historical delete
    # actions.  Task 5 creates the complete final access-log index set and
    # replaces all four FKs atomically, so FK facts become final only once that
    # set is present.  This keeps a clean Phase 3 database repairable.
    has_complete_final_indexes = not missing_indexes
    if mode == "post" or has_complete_final_indexes:
        for name, expected in EXPECTED_ACCESS_LOG_FOREIGN_KEYS.items():
            actual = named_foreign_keys.get(name)
            if actual is None:
                continue
            if actual != expected:
                return _error(
                    mode=mode,
                    error_code="access_log_foreign_key_mismatch",
                    detail=f"access_logs.{name}",
                    schema="drifted",
                    indexes="missing" if missing_indexes else "valid",
                    target_url_ondelete=normalized_ondelete,
                )

    if mode == "post":
        if missing_indexes:
            return _error(
                mode=mode,
                error_code="required_index_missing",
                detail=",".join(missing_indexes),
                schema="incomplete",
                indexes="missing",
                target_url_ondelete=normalized_ondelete,
            )
        if foreign_key_actions is None or foreign_keys_by_name is None:
            return _error(
                mode=mode,
                error_code="access_log_foreign_key_unknown",
                detail="access_logs",
                schema="drifted",
                indexes="valid",
                target_url_ondelete=normalized_ondelete,
            )
        for name, expected in EXPECTED_ACCESS_LOG_FOREIGN_KEYS.items():
            if named_foreign_keys.get(name) != expected:
                return _error(
                    mode=mode,
                    error_code="access_log_foreign_key_mismatch",
                    detail=f"access_logs.{name}",
                    schema="drifted",
                    indexes="valid",
                    target_url_ondelete=normalized_ondelete,
                )
        for column, expected in EXPECTED_ONDELETE.items():
            if foreign_key_actions.get(column) != expected:
                return _error(
                    mode=mode,
                    error_code="access_log_foreign_key_mismatch",
                    detail=f"access_logs.{column}",
                    schema="drifted",
                    indexes="valid",
                    target_url_ondelete=normalized_ondelete,
                )

    if missing_indexes:
        return {
            "mode": mode,
            "status": "ok",
            "schema": "repairable",
            "indexes": "missing",
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
    dict[str, dict[str, str | None]],
    dict[str, str | None],
    dict[str, tuple[str, str, str | None]],
]:
    engine = create_engine(database_url)
    try:
        inspector = inspect(engine)
        tables = set(inspector.get_table_names())
        indexes: dict[str, dict[str, tuple[str, ...]]] = {}
        uniqueness: dict[str, dict[str, bool | None]] = {}
        sorting: dict[str, dict[str, dict[str, tuple[str, ...]] | None]] = {}
        predicates: dict[str, dict[str, str | None]] = {}
        for table_name, definitions in EXPECTED_INDEXES.items():
            (
                indexes[table_name],
                uniqueness[table_name],
                sorting[table_name],
                predicates[table_name],
            ) = {}, {}, {}, {}
            if table_name not in tables:
                continue
            for index in inspector.get_indexes(table_name):
                name = index.get("name")
                if name not in definitions:
                    continue
                indexes[table_name][name] = tuple(index.get("column_names") or ())
                unique = index.get("unique")
                uniqueness[table_name][name] = (
                    unique if isinstance(unique, bool) else None
                )
                raw_sorting = index.get("column_sorting")
                sorting[table_name][name] = (
                    {key: tuple(value) for key, value in raw_sorting.items()}
                    if isinstance(raw_sorting, dict)
                    else {}
                )
                options = index.get("dialect_options")
                predicates[table_name][name] = (
                    _normalize_predicate(options.get("postgresql_where"))
                    if isinstance(options, dict)
                    else None
                )
        foreign_key_actions: dict[str, str | None] = {}
        foreign_keys_by_name: dict[str, tuple[str, str, str | None]] = {}
        if "access_logs" in tables:
            for foreign_key in inspector.get_foreign_keys("access_logs"):
                columns = foreign_key.get("constrained_columns")
                if not isinstance(columns, list) or len(columns) != 1:
                    continue
                ondelete = foreign_key.get("options", {}).get("ondelete")
                normalized_action = (
                    ondelete.upper() if isinstance(ondelete, str) else None
                )
                foreign_key_actions[columns[0]] = normalized_action
                name = foreign_key.get("name")
                referred_table = foreign_key.get("referred_table")
                if isinstance(name, str) and isinstance(referred_table, str):
                    foreign_keys_by_name[name] = (
                        columns[0],
                        referred_table,
                        normalized_action,
                    )
        return (
            tables,
            indexes,
            uniqueness,
            sorting,
            predicates,
            foreign_key_actions,
            foreign_keys_by_name,
        )
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
        print(
            json.dumps(
                {
                    "mode": None,
                    "status": "error",
                    "error_code": "invalid_arguments",
                    "detail": "mode",
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 2
    try:
        from app.core.config import settings

        facts = inspect_schema(settings.database_url)
        result = evaluate_schema(
            mode=parsed.mode,
            tables=facts[0],
            indexes=facts[1],
            target_url_ondelete=facts[5].get("target_url_id"),
            index_uniqueness=facts[2],
            index_sorting=facts[3],
            index_predicates=facts[4],
            foreign_key_actions=facts[5],
            foreign_keys_by_name=facts[6],
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
