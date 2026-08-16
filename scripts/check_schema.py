import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from sqlalchemy import create_engine, inspect, text

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Each definition is a final Phase 5 object, not a subset chosen for a
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
        "idx_short_links_name_trgm": {
            "columns": (None,),
            "unique": False,
            "sorting": {},
            "predicate": None,
        },
        "idx_short_links_domain_code_pattern": {
            "columns": ("domain_id", "short_code"),
            "unique": False,
            "sorting": {},
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
            "columns": ("domain_id", "accessed_at", "id"),
            "unique": False,
            "sorting": {"accessed_at": ("desc",), "id": ("desc",)},
            "predicate": None,
        },
        "idx_access_logs_link_accessed_at": {
            "columns": ("short_link_id", "accessed_at", "id"),
            "unique": False,
            "sorting": {"accessed_at": ("desc",), "id": ("desc",)},
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
            "columns": ("domain_id", "result", "accessed_at", "id"),
            "unique": False,
            "sorting": {"accessed_at": ("desc",), "id": ("desc",)},
            "predicate": None,
        },
        "idx_access_logs_domain_country_accessed_at": {
            "columns": ("domain_id", "country", "accessed_at", "id"),
            "unique": False,
            "sorting": {"accessed_at": ("desc",), "id": ("desc",)},
            "predicate": None,
        },
    },
}
PHASE4_LOG_INDEXES = {
    "idx_access_logs_domain_accessed_at": (("domain_id", "accessed_at"), {"accessed_at": ("desc",)}),
    "idx_access_logs_link_accessed_at": (("short_link_id", "accessed_at"), {"accessed_at": ("desc",)}),
    "idx_access_logs_domain_result_accessed_at": (("domain_id", "result", "accessed_at"), {"accessed_at": ("desc",)}),
    "idx_access_logs_domain_country_accessed_at": (("domain_id", "country", "accessed_at"), {"accessed_at": ("desc",)}),
}
PHASE5_INDEX_DETAILS = {
    "idx_short_links_name_trgm": ("gin", ("lower((name)::text)",), ("gin_trgm_ops",)),
    "idx_short_links_domain_code_pattern": ("btree", (), ("", "varchar_pattern_ops")),
}
for _name in PHASE4_LOG_INDEXES:
    _definition = EXPECTED_INDEXES["access_logs"][_name]
    PHASE5_INDEX_DETAILS.setdefault(
        _name, ("btree", (), tuple("" for _ in _definition["columns"]))
    )
PHASE5_CHECK = "ck_short_links_short_code_canonical"
PHASE5_EXTENSION = "pg_trgm"
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
    index_using: dict[str, dict[str, str | None]] | None = None,
    index_expressions: dict[str, dict[str, tuple[str, ...] | None]] | None = None,
    index_operator_classes: dict[str, dict[str, tuple[str, ...] | None]] | None = None,
    schema_checks: dict[str, set[str]] | None = None,
    extensions: set[str] | None = None,
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
    phase4_log_index = lambda table, name, columns, sorting: (
        table == "access_logs"
        and name in PHASE4_LOG_INDEXES
        and columns == PHASE4_LOG_INDEXES[name][0]
        and sorting == PHASE4_LOG_INDEXES[name][1]
    )
    missing_indexes: list[str] = []
    for table_name, definitions in EXPECTED_INDEXES.items():
        for index_name, expected in definitions.items():
            identifier = f"{table_name}.{index_name}"
            actual_columns = indexes.get(table_name, {}).get(index_name)
            if actual_columns is None:
                missing_indexes.append(identifier)
                continue
            actual_sorting = (
                index_sorting.get(table_name, {}).get(index_name)
                if index_sorting is not None
                else None
            )
            is_phase4_log = phase4_log_index(
                table_name, index_name, actual_columns, actual_sorting
            )
            if actual_columns != expected["columns"] and not is_phase4_log:
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
            if actual_sorting != expected["sorting"] and not is_phase4_log:
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
        if foreign_key_actions is None or foreign_keys_by_name is None:
            return _error(
                mode=mode,
                error_code="access_log_foreign_key_unknown",
                detail="access_logs",
                schema="drifted",
                indexes="missing" if missing_indexes else "valid",
                target_url_ondelete=normalized_ondelete,
            )
        for name, expected in EXPECTED_ACCESS_LOG_FOREIGN_KEYS.items():
            actual = named_foreign_keys.get(name)
            if actual is None:
                return _error(
                    mode=mode,
                    error_code="access_log_foreign_key_missing",
                    detail=f"access_logs.{name}",
                    schema="drifted",
                    indexes="missing" if missing_indexes else "valid",
                    target_url_ondelete=normalized_ondelete,
                )
            if actual != expected:
                return _error(
                    mode=mode,
                    error_code="access_log_foreign_key_mismatch",
                    detail=f"access_logs.{name}",
                    schema="drifted",
                    indexes="missing" if missing_indexes else "valid",
                    target_url_ondelete=normalized_ondelete,
                )
        for column, expected in EXPECTED_ONDELETE.items():
            actual = foreign_key_actions.get(column)
            if actual is None:
                return _error(
                    mode=mode,
                    error_code="access_log_foreign_key_missing",
                    detail=f"access_logs.{column}",
                    schema="drifted",
                    indexes="missing" if missing_indexes else "valid",
                    target_url_ondelete=normalized_ondelete,
                )
            if actual != expected:
                return _error(
                    mode=mode,
                    error_code="access_log_foreign_key_mismatch",
                    detail=f"access_logs.{column}",
                    schema="drifted",
                    indexes="missing" if missing_indexes else "valid",
                    target_url_ondelete=normalized_ondelete,
                )

    phase4_predecessor = (
        all(
            indexes.get("access_logs", {}).get(name) == columns
            and (index_sorting or {}).get("access_logs", {}).get(name) == sorting
            and uniqueness.get("access_logs", {}).get(name) is False
            and _normalize_predicate(predicates.get("access_logs", {}).get(name))
            is None
            and (index_using or {}).get("access_logs", {}).get(name) == "btree"
            and (index_expressions or {}).get("access_logs", {}).get(name) == ()
            and (index_operator_classes or {}).get("access_logs", {}).get(name)
            == tuple("" for _ in columns)
            for name, (columns, sorting) in PHASE4_LOG_INDEXES.items()
        )
        and "idx_short_links_name_trgm"
        not in indexes.get("short_links", {})
        and "idx_short_links_domain_code_pattern"
        not in indexes.get("short_links", {})
        and PHASE5_CHECK not in (schema_checks or {}).get("short_links", set())
        and PHASE5_EXTENSION not in (extensions or set())
    )
    if mode == "pre" and phase4_predecessor and set(missing_indexes) == {
        "short_links.idx_short_links_name_trgm",
        "short_links.idx_short_links_domain_code_pattern",
    }:
        return {
            "mode": mode,
            "status": "ok",
            "schema": "repairable",
            "indexes": "missing",
            "target_url_ondelete": normalized_ondelete,
        }

    if mode == "post" and missing_indexes:
        return _error(
            mode=mode,
            error_code="required_index_missing",
            detail=",".join(missing_indexes),
            schema="incomplete",
            indexes="missing",
            target_url_ondelete=normalized_ondelete,
        )
    if missing_indexes:
        return _error(
            mode=mode,
            error_code="required_index_missing",
            detail=",".join(missing_indexes),
            schema="incomplete",
            indexes="missing",
            target_url_ondelete=normalized_ondelete,
        )
    if extensions is None or PHASE5_EXTENSION not in extensions:
        return _error(
            mode=mode,
            error_code="required_extension_missing",
            detail=PHASE5_EXTENSION,
            schema="incomplete",
            indexes="invalid",
            target_url_ondelete=normalized_ondelete,
        )
    if PHASE5_CHECK not in (schema_checks or {}).get("short_links", set()):
        return _error(
            mode=mode,
            error_code="required_check_missing",
            detail=f"short_links.{PHASE5_CHECK}",
            schema="incomplete",
            indexes="invalid",
            target_url_ondelete=normalized_ondelete,
        )
    for name, (expected_using, expected_expressions, expected_operator_classes) in PHASE5_INDEX_DETAILS.items():
        table_name = "short_links" if name.startswith("idx_short_links") else "access_logs"
        identifier = f"{table_name}.{name}"
        actual_using = (index_using or {}).get(table_name, {}).get(name)
        actual_expressions = (index_expressions or {}).get(table_name, {}).get(name)
        actual_operator_classes = (index_operator_classes or {}).get(table_name, {}).get(name)
        if actual_operator_classes is None:
            return _error(
                mode=mode,
                error_code="index_operator_class_unknown",
                detail=identifier,
                schema="drifted",
                indexes="invalid",
                target_url_ondelete=normalized_ondelete,
            )
        if actual_operator_classes != expected_operator_classes:
            return _error(
                mode=mode,
                error_code="index_operator_class_mismatch",
                detail=identifier,
                schema="drifted",
                indexes="invalid",
                target_url_ondelete=normalized_ondelete,
            )
        if actual_using != expected_using or actual_expressions != expected_expressions:
            return _error(
                mode=mode,
                error_code="index_definition_mismatch",
                detail=identifier,
                schema="drifted",
                indexes="invalid",
                target_url_ondelete=normalized_ondelete,
            )
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
    dict[str, dict[str, str | None]],
    dict[str, dict[str, tuple[str, ...] | None]],
    dict[str, dict[str, tuple[str, ...] | None]],
    dict[str, set[str]],
    set[str],
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
        using: dict[str, dict[str, str | None]] = {}
        expressions: dict[str, dict[str, tuple[str, ...] | None]] = {}
        operator_classes: dict[str, dict[str, tuple[str, ...] | None]] = {}
        schema_checks: dict[str, set[str]] = {}
        for table_name, definitions in EXPECTED_INDEXES.items():
            (
                indexes[table_name],
                uniqueness[table_name],
                sorting[table_name],
                predicates[table_name],
                using[table_name],
                expressions[table_name],
                operator_classes[table_name],
            ) = {}, {}, {}, {}, {}, {}, {}
            if table_name not in tables:
                continue
            with engine.connect() as connection:
                catalog_indexes = {
                    row["name"]: row
                    for row in connection.execute(
                        text(
                            """
                            SELECT index_class.relname AS name,
                                   access_method.amname AS using,
                                   pg_get_expr(index_data.indexprs, index_data.indrelid) AS expressions,
                                   array_agg(CASE WHEN operator_class.opcdefault THEN '' ELSE operator_class.opcname END ORDER BY ordinality) AS operator_classes
                            FROM pg_index AS index_data
                            JOIN pg_class AS table_class ON table_class.oid = index_data.indrelid
                            JOIN pg_namespace AS namespace ON namespace.oid = table_class.relnamespace
                            JOIN pg_class AS index_class ON index_class.oid = index_data.indexrelid
                            JOIN pg_am AS access_method ON access_method.oid = index_class.relam
                            JOIN LATERAL unnest(index_data.indclass) WITH ORDINALITY AS class_ids(operator_class_id, ordinality) ON true
                            JOIN pg_opclass AS operator_class ON operator_class.oid = class_ids.operator_class_id
                            WHERE namespace.nspname = current_schema()
                              AND table_class.relname = :table_name
                            GROUP BY index_class.relname, access_method.amname,
                                     index_data.indexprs, index_data.indrelid
                            """
                        ),
                        {"table_name": table_name},
                    ).mappings()
                }
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
                details = catalog_indexes.get(name, {})
                using[table_name][name] = details.get("using")
                raw_expression = details.get("expressions")
                expressions[table_name][name] = (
                    (raw_expression,) if isinstance(raw_expression, str) else ()
                )
                raw_operator_classes = details.get("operator_classes")
                operator_classes[table_name][name] = (
                    tuple(raw_operator_classes)
                    if isinstance(raw_operator_classes, (list, tuple))
                    else None
                )
            schema_checks[table_name] = {
                check["name"]
                for check in inspector.get_check_constraints(table_name)
                if isinstance(check.get("name"), str)
            }
        with engine.connect() as connection:
            extensions = set(connection.scalars(text("SELECT extname FROM pg_extension")))
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
            using,
            expressions,
            operator_classes,
            schema_checks,
            extensions,
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
            target_url_ondelete=facts[10].get("target_url_id"),
            index_uniqueness=facts[2],
            index_sorting=facts[3],
            index_predicates=facts[4],
            index_using=facts[5],
            index_expressions=facts[6],
            index_operator_classes=facts[7],
            schema_checks=facts[8],
            extensions=facts[9],
            foreign_key_actions=facts[10],
            foreign_keys_by_name=facts[11],
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
