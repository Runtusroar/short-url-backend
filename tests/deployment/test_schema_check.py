import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from scripts.check_schema import (
    CURRENT_APPLICATION_TABLES,
    EXPECTED_ACCESS_LOG_FOREIGN_KEYS,
    EXPECTED_INDEXES,
    EXPECTED_ONDELETE,
    evaluate_schema,
)
from tests.migrations.support import run_alembic

ROOT = Path(__file__).resolve().parents[2]


def _ready_indexes():
    return {
        table: {name: definition["columns"] for name, definition in definitions.items()}
        for table, definitions in EXPECTED_INDEXES.items()
    }


def _ready_uniqueness():
    return {
        table: {name: definition["unique"] for name, definition in definitions.items()}
        for table, definitions in EXPECTED_INDEXES.items()
    }


def _ready_sorting():
    return {
        table: {name: definition["sorting"] for name, definition in definitions.items()}
        for table, definitions in EXPECTED_INDEXES.items()
    }


def _ready_predicates():
    return {
        table: {
            name: definition["predicate"] for name, definition in definitions.items()
        }
        for table, definitions in EXPECTED_INDEXES.items()
    }


def _ready_foreign_keys():
    return dict(EXPECTED_ONDELETE)


def _ready_named_foreign_keys():
    return dict(EXPECTED_ACCESS_LOG_FOREIGN_KEYS)


def _evaluate(*, mode="post", **overrides):
    values = {
        "mode": mode,
        "tables": set(CURRENT_APPLICATION_TABLES),
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
        tables=set(CURRENT_APPLICATION_TABLES),
        indexes={table: {} for table in EXPECTED_INDEXES},
        target_url_ondelete=None,
    )
    assert result["status"] == "ok"
    assert result["schema"] == "repairable"


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
        for table, definitions in EXPECTED_INDEXES.items()
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
