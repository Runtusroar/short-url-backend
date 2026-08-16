import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from scripts.check_schema import evaluate_schema
from tests.migrations.support import run_alembic

ROOT = Path(__file__).resolve().parents[2]
APPLICATION_TABLES = {
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
FINAL_INDEXES = {
    "domains": {"uq_domains_one_default": (("is_default",), True, {}, "is_default")},
    "user_domains": {"idx_user_domains_domain": (("domain_id",), False, {}, None)},
    "short_links": {
        "idx_short_links_domain_created_at": (
            ("domain_id", "created_at"),
            False,
            {"created_at": ("desc",)},
            None,
        ),
        "idx_short_links_domain_owner_created_at": (
            ("domain_id", "owner_id", "created_at"),
            False,
            {"created_at": ("desc",)},
            None,
        ),
    },
    "access_rules": {
        "idx_access_rules_link_active_priority": (
            ("short_link_id", "priority", "id"),
            False,
            {},
            "is_active",
        )
    },
    "ip_blacklist": {
        "idx_ip_blacklist_active_expires_at": (
            ("expires_at",),
            False,
            {},
            "removed_at IS NULL",
        )
    },
    "access_logs": {
        "idx_access_logs_domain_accessed_at": (
            ("domain_id", "accessed_at"),
            False,
            {"accessed_at": ("desc",)},
            None,
        ),
        "idx_access_logs_link_accessed_at": (
            ("short_link_id", "accessed_at"),
            False,
            {"accessed_at": ("desc",)},
            None,
        ),
        "idx_access_logs_link_access_date": (
            ("short_link_id", "access_date"),
            False,
            {"access_date": ("desc",)},
            None,
        ),
        "idx_access_logs_link_client_ip_dedup": (
            ("short_link_id", "client_ip", "dedup_bucket"),
            False,
            {},
            None,
        ),
        "idx_access_logs_domain_result_accessed_at": (
            ("domain_id", "result", "accessed_at"),
            False,
            {"accessed_at": ("desc",)},
            None,
        ),
        "idx_access_logs_domain_country_accessed_at": (
            ("domain_id", "country", "accessed_at"),
            False,
            {"accessed_at": ("desc",)},
            None,
        ),
    },
}
FINAL_ACCESS_LOG_FKS = {
    "fk_access_logs_short_link": ("short_link_id", "short_links", "RESTRICT"),
    "fk_access_logs_domain": ("domain_id", "domains", "RESTRICT"),
    "fk_access_logs_target_url": ("target_url_id", "target_urls", "SET NULL"),
    "fk_access_logs_matched_rule": ("matched_rule_id", "access_rules", "SET NULL"),
}
FINAL_ACCESS_LOG_ACTIONS = {
    "short_link_id": "RESTRICT",
    "domain_id": "RESTRICT",
    "target_url_id": "SET NULL",
    "matched_rule_id": "SET NULL",
}


def _ready_indexes():
    return {
        table: {name: definition[0] for name, definition in definitions.items()}
        for table, definitions in FINAL_INDEXES.items()
    }


def _ready_uniqueness():
    return {
        table: {name: definition[1] for name, definition in definitions.items()}
        for table, definitions in FINAL_INDEXES.items()
    }


def _ready_sorting():
    return {
        table: {name: definition[2] for name, definition in definitions.items()}
        for table, definitions in FINAL_INDEXES.items()
    }


def _ready_predicates():
    return {
        table: {name: definition[3] for name, definition in definitions.items()}
        for table, definitions in FINAL_INDEXES.items()
    }


def _ready_foreign_keys():
    return dict(FINAL_ACCESS_LOG_ACTIONS)


def _ready_named_foreign_keys():
    return dict(FINAL_ACCESS_LOG_FKS)


def _final_index_parameters():
    return [
        (table, name, facts)
        for table, definitions in FINAL_INDEXES.items()
        for name, facts in definitions.items()
    ]


def _evaluate(*, mode="post", **overrides):
    values = {
        "mode": mode,
        "tables": set(APPLICATION_TABLES),
        "indexes": _ready_indexes(),
        "target_url_ondelete": "SET NULL",
        "index_uniqueness": _ready_uniqueness(),
        "index_sorting": _ready_sorting(),
        "index_predicates": _ready_predicates(),
        "foreign_key_actions": _ready_foreign_keys(),
        "foreign_keys_by_name": _ready_named_foreign_keys(),
    }
    values.update(overrides)
    return evaluate_schema(**values)


def _run_schema_check(database_url: str, mode: str) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["APP_ENV"] = "dev"
    environment["DATABASE_URL"] = database_url
    return subprocess.run(
        [sys.executable, "scripts/check_schema.py", "--mode", mode],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )


def _public_json(result: subprocess.CompletedProcess[str], database_url: str):
    assert result.stderr == ""
    assert len(result.stdout.splitlines()) == 1
    assert database_url not in result.stdout
    assert "SELECT" not in result.stdout
    assert "Traceback" not in result.stdout
    return json.loads(result.stdout)


def test_pre_allows_empty_database_and_repairable_phase_three_schema():
    assert (
        evaluate_schema(mode="pre", tables=set(), indexes={}, target_url_ondelete=None)[
            "schema"
        ]
        == "empty"
    )
    result = evaluate_schema(
        mode="pre",
        tables=set(APPLICATION_TABLES),
        indexes={table: {} for table in FINAL_INDEXES},
        target_url_ondelete=None,
    )
    assert result["status"] == "ok"
    assert result["schema"] == "repairable"


def test_pure_evaluate_runs_with_an_unreachable_database_url_without_shared_ddl():
    environment = os.environ.copy()
    environment["DATABASE_URL"] = (
        "postgresql+psycopg://fixture:fixture@127.0.0.1:1/unreachable"
    )
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            __file__,
            "-k",
            "pre_allows_empty_database_and_repairable_phase_three_schema",
        ],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_partial_schema_fails_closed():
    result = evaluate_schema(
        mode="pre", tables={"users"}, indexes={}, target_url_ondelete=None
    )
    assert result["status"] == "error"
    assert result["error_code"] == "schema_incomplete"


@pytest.mark.parametrize(
    ("table", "name"),
    [
        (table, name)
        for table, definitions in FINAL_INDEXES.items()
        for name in definitions
    ],
)
def test_pre_rejects_each_present_final_index_with_unknown_or_wrong_facts(table, name):
    indexes = _ready_indexes()
    columns = indexes[table][name]
    indexes[table][name] = (
        tuple(reversed(columns)) if len(columns) > 1 else ("wrong_column",)
    )
    result = _evaluate(mode="pre", indexes=indexes)
    assert result["error_code"] == "index_definition_mismatch"
    assert result["detail"] == f"{table}.{name}"


def test_pre_rejects_present_final_index_with_wrong_predicate():
    predicates = _ready_predicates()
    predicates["domains"]["uq_domains_one_default"] = "NOT is_default"
    result = _evaluate(mode="pre", index_predicates=predicates)
    assert result["error_code"] == "index_predicate_mismatch"
    assert result["detail"] == "domains.uq_domains_one_default"


def test_pre_rejects_present_final_index_with_wrong_sorting_or_uniqueness():
    sorting = _ready_sorting()
    sorting["short_links"]["idx_short_links_domain_created_at"] = {}
    assert (
        _evaluate(mode="pre", index_sorting=sorting)["error_code"]
        == "index_sorting_mismatch"
    )
    uniqueness = _ready_uniqueness()
    uniqueness["domains"]["uq_domains_one_default"] = False
    assert (
        _evaluate(mode="pre", index_uniqueness=uniqueness)["error_code"]
        == "index_uniqueness_mismatch"
    )


@pytest.mark.parametrize(
    ("overrides", "error_code"),
    [
        ({"index_uniqueness": None}, "index_uniqueness_unknown"),
        ({"index_sorting": None}, "index_sorting_unknown"),
        ({"foreign_key_actions": None}, "access_log_foreign_key_unknown"),
        ({"foreign_keys_by_name": None}, "access_log_foreign_key_unknown"),
    ],
)
def test_complete_final_indexes_fail_closed_for_unknown_metadata(overrides, error_code):
    assert _evaluate(mode="pre", **overrides)["error_code"] == error_code
    assert _evaluate(mode="post", **overrides)["error_code"] == error_code


def test_pre_rejects_present_final_access_log_fk_action_conflict():
    foreign_keys = _ready_named_foreign_keys()
    foreign_keys["fk_access_logs_domain"] = ("domain_id", "domains", "CASCADE")
    result = _evaluate(mode="pre", foreign_keys_by_name=foreign_keys)
    assert result["error_code"] == "access_log_foreign_key_mismatch"
    assert result["detail"] == "access_logs.fk_access_logs_domain"


def test_post_requires_every_final_object_and_metadata():
    indexes = _ready_indexes()
    del indexes["ip_blacklist"]["idx_ip_blacklist_active_expires_at"]
    assert _evaluate(indexes=indexes)["error_code"] == "required_index_missing"
    assert _evaluate(index_predicates=None)["error_code"] == "index_predicate_unknown"
    assert (
        _evaluate(foreign_keys_by_name=None)["error_code"]
        == "access_log_foreign_key_unknown"
    )


def test_post_reports_ready_final_contract():
    assert _evaluate() == {
        "mode": "post",
        "status": "ok",
        "schema": "ready",
        "indexes": "valid",
        "target_url_ondelete": "SET NULL",
    }


@pytest.mark.parametrize("mode", ("pre", "post"))
def test_complete_final_indexes_fail_closed_when_named_fk_map_is_missing(mode):
    result = _evaluate(mode=mode, foreign_keys_by_name={})
    assert result["status"] == "error"
    assert result["error_code"] == "access_log_foreign_key_missing"
    assert result["detail"] == "access_logs.fk_access_logs_short_link"


@pytest.mark.parametrize("mode", ("pre", "post"))
def test_complete_final_indexes_fail_closed_when_fk_action_map_is_missing(mode):
    result = _evaluate(mode=mode, foreign_key_actions={})
    assert result["status"] == "error"
    assert result["error_code"] == "access_log_foreign_key_missing"
    assert result["detail"] == "access_logs.short_link_id"


def test_cli_pre_and_post_use_one_sanitized_json_object(migration_database_url):
    pre = _run_schema_check(migration_database_url, "pre")
    assert pre.returncode == 0
    assert _public_json(pre, migration_database_url)["schema"] == "empty"

    run_alembic(migration_database_url, "upgrade", "d6e8f0a21b35")
    historical = _run_schema_check(migration_database_url, "pre")
    historical_payload = _public_json(historical, migration_database_url)
    assert historical.returncode == 0, historical_payload
    assert historical_payload["schema"] == "repairable"

    run_alembic(migration_database_url, "upgrade", "head")
    post = _run_schema_check(migration_database_url, "post")
    assert post.returncode == 0
    assert _public_json(post, migration_database_url)["schema"] == "ready"


def test_cli_pre_rejects_same_named_partial_index_conflict(migration_database_url):
    run_alembic(migration_database_url, "upgrade", "head")
    engine = create_engine(migration_database_url)
    try:
        with engine.begin() as connection:
            connection.execute(text("DROP INDEX uq_domains_one_default"))
            connection.execute(
                text(
                    "CREATE UNIQUE INDEX uq_domains_one_default ON domains (is_default)"
                )
            )
    finally:
        engine.dispose()
    result = _run_schema_check(migration_database_url, "pre")
    assert result.returncode == 1
    payload = _public_json(result, migration_database_url)
    assert payload["error_code"] == "index_predicate_mismatch"
    assert payload["detail"] == "domains.uq_domains_one_default"


def test_cli_pre_rejects_same_named_nonunique_replacement_of_unique_index(
    migration_database_url,
):
    run_alembic(migration_database_url, "upgrade", "head")
    engine = create_engine(migration_database_url)
    try:
        with engine.begin() as connection:
            connection.execute(text("DROP INDEX uq_domains_one_default"))
            connection.execute(
                text(
                    "CREATE INDEX uq_domains_one_default ON domains (is_default) "
                    "WHERE is_default"
                )
            )
    finally:
        engine.dispose()

    result = _run_schema_check(migration_database_url, "pre")
    assert result.returncode == 1
    payload = _public_json(result, migration_database_url)
    assert payload["error_code"] == "index_uniqueness_mismatch"
    assert payload["detail"] == "domains.uq_domains_one_default"


@pytest.mark.parametrize("mode", ("pre", "post"))
@pytest.mark.parametrize(("table", "name", "facts"), _final_index_parameters())
def test_cli_rejects_each_final_index_with_wrong_columns(
    migration_database_url, mode, table, name, facts
):
    columns, unique, _sorting, _predicate = facts
    replacement = "id" if columns != ("id",) else "created_at"
    create = "CREATE " + ("UNIQUE " if unique else "") + "INDEX "
    run_alembic(migration_database_url, "upgrade", "head")
    engine = create_engine(migration_database_url)
    try:
        with engine.begin() as connection:
            connection.execute(text(f"DROP INDEX {name}"))
            connection.execute(text(f"{create}{name} ON {table} ({replacement})"))
    finally:
        engine.dispose()

    result = _run_schema_check(migration_database_url, mode)
    assert result.returncode == 1
    payload = _public_json(result, migration_database_url)
    assert payload["error_code"] == "index_definition_mismatch"
    assert payload["detail"] == f"{table}.{name}"


@pytest.mark.parametrize("mode", ("pre", "post"))
@pytest.mark.parametrize(
    ("name", "columns"),
    [
        ("idx_access_logs_domain_accessed_at", "domain_id, accessed_at"),
        ("idx_access_logs_link_accessed_at", "short_link_id, accessed_at"),
        ("idx_access_logs_link_access_date", "short_link_id, access_date"),
        (
            "idx_access_logs_domain_result_accessed_at",
            "domain_id, result, accessed_at",
        ),
        (
            "idx_access_logs_domain_country_accessed_at",
            "domain_id, country, accessed_at",
        ),
    ],
)
def test_cli_rejects_each_descending_access_log_index_replaced_by_ascending(
    migration_database_url, mode, name, columns
):
    run_alembic(migration_database_url, "upgrade", "head")
    engine = create_engine(migration_database_url)
    try:
        with engine.begin() as connection:
            connection.execute(text(f"DROP INDEX {name}"))
            connection.execute(text(f"CREATE INDEX {name} ON access_logs ({columns})"))
    finally:
        engine.dispose()

    result = _run_schema_check(migration_database_url, mode)
    assert result.returncode == 1
    payload = _public_json(result, migration_database_url)
    assert payload["error_code"] == "index_sorting_mismatch"
    assert payload["detail"] == f"access_logs.{name}"


@pytest.mark.parametrize("mode", ("pre", "post"))
@pytest.mark.parametrize(
    ("constraint", "column", "parent_table"),
    [
        ("fk_access_logs_short_link", "short_link_id", "short_links"),
        ("fk_access_logs_domain", "domain_id", "domains"),
        ("fk_access_logs_target_url", "target_url_id", "target_urls"),
        ("fk_access_logs_matched_rule", "matched_rule_id", "access_rules"),
    ],
)
def test_cli_rejects_each_access_log_fk_with_wrong_action(
    migration_database_url, mode, constraint, column, parent_table
):
    run_alembic(migration_database_url, "upgrade", "head")
    engine = create_engine(migration_database_url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(f"ALTER TABLE access_logs DROP CONSTRAINT {constraint}")
            )
            connection.execute(
                text(
                    "ALTER TABLE access_logs "
                    f"ADD CONSTRAINT {constraint} FOREIGN KEY ({column}) "
                    f"REFERENCES {parent_table}(id) ON DELETE CASCADE"
                )
            )
    finally:
        engine.dispose()

    result = _run_schema_check(migration_database_url, mode)
    assert result.returncode == 1
    payload = _public_json(result, migration_database_url)
    assert payload["error_code"] == "access_log_foreign_key_mismatch"
    assert payload["detail"] == f"access_logs.{constraint}"


@pytest.mark.parametrize("mode", ("pre", "post"))
def test_cli_rejects_missing_named_access_log_fk(migration_database_url, mode):
    run_alembic(migration_database_url, "upgrade", "head")
    engine = create_engine(migration_database_url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text("ALTER TABLE access_logs DROP CONSTRAINT fk_access_logs_domain")
            )
    finally:
        engine.dispose()

    result = _run_schema_check(migration_database_url, mode)
    assert result.returncode == 1
    payload = _public_json(result, migration_database_url)
    assert payload["error_code"] == "access_log_foreign_key_missing"
    assert payload["detail"] == "access_logs.fk_access_logs_domain"


def test_cli_pre_rejects_partial_application_schema(migration_database_url):
    engine = create_engine(migration_database_url)
    try:
        with engine.begin() as connection:
            connection.execute(text("CREATE TABLE users (id INTEGER PRIMARY KEY)"))
    finally:
        engine.dispose()

    result = _run_schema_check(migration_database_url, "pre")
    assert result.returncode == 1
    payload = _public_json(result, migration_database_url)
    assert payload["error_code"] == "schema_incomplete"


def test_cli_connection_failure_is_single_sanitized_json_object():
    database_url = (
        "missing-driver://database-user:database-password@database-host/database-name"
    )
    result = _run_schema_check(database_url, "pre")
    combined_output = result.stdout + result.stderr

    assert result.returncode == 1
    assert _public_json(result, database_url) == {
        "mode": "pre",
        "status": "error",
        "error_code": "schema_inspection_failed",
        "detail": "database_schema",
    }
    for secret in (
        "database-user",
        "database-password",
        "database-host",
        "SELECT",
        "Traceback",
    ):
        assert secret not in combined_output
