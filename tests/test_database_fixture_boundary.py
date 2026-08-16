"""Keep the shared-schema autouse bypass tightly scoped to migration tests."""

from types import SimpleNamespace

import pytest

from tests import conftest


def test_disposable_database_bypass_uses_resolved_path_boundaries(monkeypatch):
    migration_path = conftest._MIGRATION_TESTS_ROOT / "test_example.py"
    schema_check_path = conftest._SCHEMA_CHECK_TEST
    prefix_escape = conftest._TESTS_ROOT / "migrations_evil" / "test_example.py"
    normal_deployment_test = conftest._TESTS_ROOT / "deployment" / "test_other.py"

    assert conftest._uses_disposable_schema_database(migration_path)
    assert conftest._uses_disposable_schema_database(schema_check_path)
    assert not conftest._uses_disposable_schema_database(prefix_escape)
    assert not conftest._uses_disposable_schema_database(normal_deployment_test)

    def unexpected_ddl(*_args, **_kwargs):
        raise AssertionError("shared metadata DDL must not run for an allowed path")

    monkeypatch.setattr(conftest.Base.metadata, "drop_all", unexpected_ddl)
    monkeypatch.setattr(conftest.Base.metadata, "create_all", unexpected_ddl)
    fixture = conftest.setup_database.__wrapped__(
        SimpleNamespace(path=schema_check_path)
    )
    next(fixture)
    with pytest.raises(StopIteration):
        next(fixture)
