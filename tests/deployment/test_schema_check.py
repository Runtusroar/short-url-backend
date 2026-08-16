import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from scripts.check_schema import (
    EXPECTED_INDEX_SORTING,
    EXPECTED_ONDELETE,
    evaluate_schema,
)
from tests.migrations.support import run_alembic

ROOT = Path(__file__).resolve().parents[2]
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
APPLICATION_TABLES = {"access_logs"}
EXPECTED_UNIQUENESS = {
    table: {name: False for name in definitions}
    for table, definitions in EXPECTED_INDEXES.items()
}


def _ready_sorting() -> dict[str, dict[str, dict[str, tuple[str, ...]]]]:
    return {"access_logs": {name: dict(columns) for name, columns in EXPECTED_INDEX_SORTING.items()}}


def _ready_foreign_keys() -> dict[str, str]:
    return dict(EXPECTED_ONDELETE)


def run_schema_check(database_url: str, mode: str) -> subprocess.CompletedProcess[str]:
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


def assert_single_public_json(
    result: subprocess.CompletedProcess[str], database_url: str
) -> dict[str, object]:
    assert result.stderr == ""
    assert len(result.stdout.splitlines()) == 1
    assert database_url not in result.stdout
    return json.loads(result.stdout)


def test_pre_allows_empty_database():
    result = evaluate_schema(
        mode="pre", tables=set(), indexes={}, target_url_ondelete=None
    )

    assert result == {
        "mode": "pre",
        "status": "ok",
        "schema": "empty",
        "indexes": "not_applicable",
        "target_url_ondelete": None,
    }


def test_pre_treats_partial_application_schema_as_incomplete():
    result = evaluate_schema(
        mode="pre", tables={"users"}, indexes={}, target_url_ondelete=None
    )

    assert result["status"] == "error"
    assert result["error_code"] == "schema_incomplete"
    assert result["detail"] == "access_logs"


def test_pre_allows_all_five_indexes_missing():
    result = evaluate_schema(
        mode="pre",
        tables=APPLICATION_TABLES,
        indexes={"short_links": {}, "access_logs": {}},
        target_url_ondelete="SET NULL",
    )

    assert result == {
        "mode": "pre",
        "status": "ok",
        "schema": "repairable",
        "indexes": "missing",
        "target_url_ondelete": "SET NULL",
    }


def test_pre_reports_old_target_url_delete_rule_without_blocking_upgrade():
    result = evaluate_schema(
        mode="pre",
        tables=APPLICATION_TABLES,
        indexes={"short_links": {}, "access_logs": {}},
        target_url_ondelete=None,
    )

    assert result == {
        "mode": "pre",
        "status": "ok",
        "schema": "repairable",
        "indexes": "missing",
        "target_url_ondelete": None,
    }


def test_pre_rejects_same_named_wrong_index_columns():
    indexes = {
        table: definitions.copy() for table, definitions in EXPECTED_INDEXES.items()
    }
    indexes["access_logs"]["idx_access_logs_link_access_date"] = (
        "access_date",
        "short_link_id",
    )

    result = evaluate_schema(
        mode="pre",
        tables=APPLICATION_TABLES,
        indexes=indexes,
        target_url_ondelete="SET NULL",
        index_uniqueness=EXPECTED_UNIQUENESS,
        index_sorting=_ready_sorting(),
        foreign_key_actions=_ready_foreign_keys(),
    )

    assert result["status"] == "error"
    assert result["error_code"] == "index_definition_mismatch"
    assert result["detail"] == "access_logs.idx_access_logs_link_access_date"


def test_pre_rejects_same_named_unique_index():
    uniqueness = {
        table: {name: False for name in definitions}
        for table, definitions in EXPECTED_INDEXES.items()
    }
    uniqueness["access_logs"]["idx_access_logs_link_access_date"] = True

    result = evaluate_schema(
        mode="pre",
        tables=APPLICATION_TABLES,
        indexes=EXPECTED_INDEXES,
        target_url_ondelete="SET NULL",
        index_uniqueness=uniqueness,
        index_sorting=_ready_sorting(),
        foreign_key_actions=_ready_foreign_keys(),
    )

    assert result["status"] == "error"
    assert result["error_code"] == "index_uniqueness_mismatch"
    assert result["detail"] == "access_logs.idx_access_logs_link_access_date"


@pytest.mark.parametrize(
    "index_uniqueness",
    [
        None,
        {
            "access_logs": {
                **{name: False for name in EXPECTED_INDEXES["access_logs"]},
                "idx_access_logs_link_access_date": None,
            }
        },
    ],
    ids=("missing", "none"),
)
def test_pre_rejects_present_index_with_unknown_uniqueness(index_uniqueness):
    result = evaluate_schema(
        mode="pre",
        tables=APPLICATION_TABLES,
        indexes=EXPECTED_INDEXES,
        target_url_ondelete="SET NULL",
        index_uniqueness=index_uniqueness,
        index_sorting=_ready_sorting(),
        foreign_key_actions=_ready_foreign_keys(),
    )

    assert result["status"] == "error"
    assert result["error_code"] == "index_uniqueness_unknown"
    expected_detail = (
        "access_logs.idx_access_logs_domain_accessed_at"
        if index_uniqueness is None
        else "access_logs.idx_access_logs_link_access_date"
    )
    assert result["detail"] == expected_detail


@pytest.mark.parametrize(
    "index_name",
    [
        "idx_access_logs_domain_accessed_at",
        "idx_access_logs_link_accessed_at",
        "idx_access_logs_link_access_date",
        "idx_access_logs_domain_result_accessed_at",
        "idx_access_logs_domain_country_accessed_at",
    ],
)
def test_pre_rejects_complete_final_indexes_with_ascending_sorting(index_name):
    sorting = _ready_sorting()
    sorting["access_logs"][index_name] = {}

    result = evaluate_schema(
        mode="pre",
        tables=APPLICATION_TABLES,
        indexes=EXPECTED_INDEXES,
        target_url_ondelete="SET NULL",
        index_uniqueness=EXPECTED_UNIQUENESS,
        index_sorting=sorting,
        foreign_key_actions=_ready_foreign_keys(),
    )

    assert result["error_code"] == "index_sorting_mismatch"
    assert result["detail"] == f"access_logs.{index_name}"


@pytest.mark.parametrize(
    ("sorting", "foreign_keys", "error_code"),
    [
        (None, _ready_foreign_keys(), "index_sorting_unknown"),
        (_ready_sorting(), None, "access_log_foreign_key_unknown"),
    ],
)
def test_pre_fails_closed_for_complete_final_schema_unknown_metadata(
    sorting, foreign_keys, error_code
):
    result = evaluate_schema(
        mode="pre",
        tables=APPLICATION_TABLES,
        indexes=EXPECTED_INDEXES,
        target_url_ondelete="SET NULL",
        index_uniqueness=EXPECTED_UNIQUENESS,
        index_sorting=sorting,
        foreign_key_actions=foreign_keys,
    )

    assert result["error_code"] == error_code


@pytest.mark.parametrize("foreign_key", sorted(EXPECTED_ONDELETE))
def test_pre_rejects_complete_final_schema_with_each_wrong_foreign_key(foreign_key):
    foreign_keys = _ready_foreign_keys()
    foreign_keys[foreign_key] = "CASCADE"

    result = evaluate_schema(
        mode="pre",
        tables=APPLICATION_TABLES,
        indexes=EXPECTED_INDEXES,
        target_url_ondelete="SET NULL",
        index_uniqueness=EXPECTED_UNIQUENESS,
        index_sorting=_ready_sorting(),
        foreign_key_actions=foreign_keys,
    )

    assert result["error_code"] == "access_log_foreign_key_mismatch"
    assert result["detail"] == f"access_logs.{foreign_key}"


def test_pre_keeps_missing_final_indexes_repairable_without_final_metadata():
    result = evaluate_schema(
        mode="pre",
        tables=APPLICATION_TABLES,
        indexes={"access_logs": {}},
        target_url_ondelete=None,
    )

    assert result["status"] == "ok"
    assert result["schema"] == "repairable"


def test_post_requires_every_expected_index():
    indexes = {
        table: definitions.copy() for table, definitions in EXPECTED_INDEXES.items()
    }
    del indexes["access_logs"]["idx_access_logs_link_client_ip_dedup"]

    result = evaluate_schema(
        mode="post",
        tables=APPLICATION_TABLES,
        indexes=indexes,
        target_url_ondelete="SET NULL",
        index_uniqueness=EXPECTED_UNIQUENESS,
        index_sorting=_ready_sorting(),
        foreign_key_actions=_ready_foreign_keys(),
    )

    assert result["status"] == "error"
    assert result["error_code"] == "required_index_missing"
    assert result["detail"] == "access_logs.idx_access_logs_link_client_ip_dedup"


def test_post_requires_target_url_set_null():
    result = evaluate_schema(
        mode="post",
        tables=APPLICATION_TABLES,
        indexes=EXPECTED_INDEXES,
        target_url_ondelete="CASCADE",
        index_uniqueness=EXPECTED_UNIQUENESS,
        index_sorting=_ready_sorting(),
        foreign_key_actions=_ready_foreign_keys(),
    )

    assert result["status"] == "error"
    assert result["error_code"] == "target_url_foreign_key_mismatch"
    assert result["detail"] == "access_logs.target_url_id"


def test_post_reports_ready_schema():
    result = evaluate_schema(
        mode="post",
        tables=APPLICATION_TABLES,
        indexes=EXPECTED_INDEXES,
        target_url_ondelete="SET NULL",
        index_uniqueness=EXPECTED_UNIQUENESS,
        index_sorting=_ready_sorting(),
        foreign_key_actions=_ready_foreign_keys(),
    )

    assert result == {
        "mode": "post",
        "status": "ok",
        "schema": "ready",
        "indexes": "valid",
        "target_url_ondelete": "SET NULL",
    }


@pytest.mark.parametrize(
    "index_name",
    [
        "idx_access_logs_domain_accessed_at",
        "idx_access_logs_link_accessed_at",
        "idx_access_logs_link_access_date",
        "idx_access_logs_domain_result_accessed_at",
        "idx_access_logs_domain_country_accessed_at",
    ],
)
def test_post_rejects_same_named_ascending_index(index_name):
    sorting = _ready_sorting()
    sorting["access_logs"][index_name] = {}

    result = evaluate_schema(
        mode="post",
        tables=APPLICATION_TABLES,
        indexes=EXPECTED_INDEXES,
        target_url_ondelete="SET NULL",
        index_uniqueness=EXPECTED_UNIQUENESS,
        index_sorting=sorting,
        foreign_key_actions=_ready_foreign_keys(),
    )

    assert result["status"] == "error"
    assert result["error_code"] == "index_sorting_mismatch"
    assert result["detail"] == f"access_logs.{index_name}"


@pytest.mark.parametrize("foreign_key", sorted(EXPECTED_ONDELETE))
def test_post_rejects_each_wrong_access_log_foreign_key_action(foreign_key):
    foreign_keys = _ready_foreign_keys()
    foreign_keys[foreign_key] = "CASCADE"

    result = evaluate_schema(
        mode="post",
        tables=APPLICATION_TABLES,
        indexes=EXPECTED_INDEXES,
        target_url_ondelete="SET NULL",
        index_uniqueness=EXPECTED_UNIQUENESS,
        index_sorting=_ready_sorting(),
        foreign_key_actions=foreign_keys,
    )

    assert result["status"] == "error"
    assert result["error_code"] == "access_log_foreign_key_mismatch"
    assert result["detail"] == f"access_logs.{foreign_key}"


@pytest.mark.parametrize(
    ("sorting", "foreign_keys", "expected_error"),
    [
        (None, _ready_foreign_keys(), "index_sorting_unknown"),
        (_ready_sorting(), None, "access_log_foreign_key_unknown"),
    ],
)
def test_post_fails_closed_when_index_or_foreign_key_metadata_is_unknown(
    sorting, foreign_keys, expected_error
):
    result = evaluate_schema(
        mode="post",
        tables=APPLICATION_TABLES,
        indexes=EXPECTED_INDEXES,
        target_url_ondelete="SET NULL",
        index_uniqueness=EXPECTED_UNIQUENESS,
        index_sorting=sorting,
        foreign_key_actions=foreign_keys,
    )

    assert result["status"] == "error"
    assert result["error_code"] == expected_error


def test_public_output_contains_no_connection_values():
    result = evaluate_schema(
        mode="post",
        tables=APPLICATION_TABLES,
        indexes={"short_links": {}, "access_logs": {}},
        target_url_ondelete=None,
    )
    public_output = json.dumps(result)

    for secret in (
        "postgresql+psycopg",
        "database-user",
        "database-password",
        "database-host",
        "SELECT",
        "Traceback",
    ):
        assert secret not in public_output


def test_cli_pre_allows_empty_disposable_database(migration_database_url):
    result = run_schema_check(migration_database_url, "pre")

    assert result.returncode == 0
    assert assert_single_public_json(result, migration_database_url) == {
        "mode": "pre",
        "status": "ok",
        "schema": "empty",
        "indexes": "not_applicable",
        "target_url_ondelete": None,
    }


def test_cli_pre_rejects_partial_application_schema(migration_database_url):
    engine = create_engine(migration_database_url)
    try:
        with engine.begin() as connection:
            connection.execute(text("CREATE TABLE users (id INTEGER PRIMARY KEY)"))
    finally:
        engine.dispose()

    result = run_schema_check(migration_database_url, "pre")

    assert result.returncode == 1
    payload = assert_single_public_json(result, migration_database_url)
    assert payload["status"] == "error"
    assert payload["error_code"] == "schema_incomplete"
    assert payload["detail"] == "access_logs"


def test_cli_pre_allows_old_head_and_reports_old_target_fk(migration_database_url):
    run_alembic(migration_database_url, "upgrade", "b1e5f6851085")

    result = run_schema_check(migration_database_url, "pre")

    assert result.returncode == 0
    assert assert_single_public_json(result, migration_database_url) == {
        "mode": "pre",
        "status": "ok",
        "schema": "repairable",
        "indexes": "missing",
        "target_url_ondelete": None,
    }


def test_cli_pre_rejects_same_named_unique_postgresql_index(
    migration_database_url,
):
    run_alembic(migration_database_url, "upgrade", "head")
    engine = create_engine(migration_database_url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "DROP INDEX idx_access_logs_link_access_date"
                )
            )
            connection.execute(
                text(
                    "CREATE UNIQUE INDEX idx_access_logs_link_access_date "
                    "ON access_logs (short_link_id, access_date DESC)"
                )
            )
    finally:
        engine.dispose()

    result = run_schema_check(migration_database_url, "pre")

    assert result.returncode == 1
    payload = assert_single_public_json(result, migration_database_url)
    assert payload["status"] == "error"
    assert payload["error_code"] == "index_uniqueness_mismatch"
    assert payload["detail"] == "access_logs.idx_access_logs_link_access_date"


def test_cli_post_requires_and_reports_repaired_head(migration_database_url):
    run_alembic(migration_database_url, "upgrade", "head")

    result = run_schema_check(migration_database_url, "post")

    assert result.returncode == 0
    assert assert_single_public_json(result, migration_database_url) == {
        "mode": "post",
        "status": "ok",
        "schema": "ready",
        "indexes": "valid",
        "target_url_ondelete": "SET NULL",
    }


@pytest.mark.parametrize(
    ("index_name", "columns"),
    [
        ("idx_access_logs_domain_accessed_at", "domain_id, accessed_at"),
        ("idx_access_logs_link_accessed_at", "short_link_id, accessed_at"),
        ("idx_access_logs_link_access_date", "short_link_id, access_date"),
        ("idx_access_logs_domain_result_accessed_at", "domain_id, result, accessed_at"),
        ("idx_access_logs_domain_country_accessed_at", "domain_id, country, accessed_at"),
    ],
)
def test_cli_pre_rejects_complete_final_schema_with_ascending_index(
    migration_database_url, index_name, columns
):
    run_alembic(migration_database_url, "upgrade", "head")
    engine = create_engine(migration_database_url)
    try:
        with engine.begin() as connection:
            connection.execute(text(f"DROP INDEX {index_name}"))
            connection.execute(text(f"CREATE INDEX {index_name} ON access_logs ({columns})"))
    finally:
        engine.dispose()

    result = run_schema_check(migration_database_url, "pre")

    assert result.returncode == 1
    assert assert_single_public_json(result, migration_database_url)["error_code"] == "index_sorting_mismatch"


@pytest.mark.parametrize(
    ("constraint", "column", "parent_table"),
    [
        ("fk_access_logs_short_link", "short_link_id", "short_links"),
        ("fk_access_logs_domain", "domain_id", "domains"),
        ("fk_access_logs_target_url", "target_url_id", "target_urls"),
        ("fk_access_logs_matched_rule", "matched_rule_id", "access_rules"),
    ],
)
def test_cli_pre_rejects_complete_final_schema_with_wrong_foreign_key(
    migration_database_url, constraint, column, parent_table
):
    run_alembic(migration_database_url, "upgrade", "head")
    engine = create_engine(migration_database_url)
    try:
        with engine.begin() as connection:
            connection.execute(text(f"ALTER TABLE access_logs DROP CONSTRAINT {constraint}"))
            connection.execute(text(
                "ALTER TABLE access_logs "
                f"ADD CONSTRAINT {constraint} FOREIGN KEY ({column}) "
                f"REFERENCES {parent_table}(id) ON DELETE CASCADE"
            ))
    finally:
        engine.dispose()

    result = run_schema_check(migration_database_url, "pre")

    assert result.returncode == 1
    assert assert_single_public_json(result, migration_database_url)["error_code"] == "access_log_foreign_key_mismatch"


@pytest.mark.parametrize(
    ("index_name", "columns"),
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
def test_cli_post_rejects_same_named_ascending_access_log_index(
    migration_database_url, index_name, columns
):
    run_alembic(migration_database_url, "upgrade", "head")
    engine = create_engine(migration_database_url)
    try:
        with engine.begin() as connection:
            connection.execute(text(f"DROP INDEX {index_name}"))
            connection.execute(
                text(f"CREATE INDEX {index_name} ON access_logs ({columns})")
            )
    finally:
        engine.dispose()

    result = run_schema_check(migration_database_url, "post")

    assert result.returncode == 1
    payload = assert_single_public_json(result, migration_database_url)
    assert payload["error_code"] == "index_sorting_mismatch"
    assert payload["detail"] == f"access_logs.{index_name}"


@pytest.mark.parametrize(
    ("constraint", "column", "parent_table"),
    [
        ("fk_access_logs_short_link", "short_link_id", "short_links"),
        ("fk_access_logs_domain", "domain_id", "domains"),
        ("fk_access_logs_target_url", "target_url_id", "target_urls"),
        ("fk_access_logs_matched_rule", "matched_rule_id", "access_rules"),
    ],
)
def test_cli_post_rejects_each_wrong_access_log_foreign_key_action(
    migration_database_url, constraint, column, parent_table
):
    run_alembic(migration_database_url, "upgrade", "head")
    engine = create_engine(migration_database_url)
    try:
        with engine.begin() as connection:
            connection.execute(text(f"ALTER TABLE access_logs DROP CONSTRAINT {constraint}"))
            connection.execute(
                text(
                    "ALTER TABLE access_logs "
                    f"ADD CONSTRAINT {constraint} FOREIGN KEY ({column}) "
                    f"REFERENCES {parent_table}(id) ON DELETE CASCADE"
                )
            )
    finally:
        engine.dispose()

    result = run_schema_check(migration_database_url, "post")

    assert result.returncode == 1
    payload = assert_single_public_json(result, migration_database_url)
    assert payload["error_code"] == "access_log_foreign_key_mismatch"
    assert payload["detail"] == f"access_logs.{column}"


def test_cli_connection_failure_is_single_sanitized_json_object():
    database_url = (
        "missing-driver://database-user:database-password@database-host/database-name"
    )

    result = run_schema_check(database_url, "pre")
    combined_output = result.stdout + result.stderr

    assert result.returncode == 1
    assert assert_single_public_json(result, database_url) == {
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
