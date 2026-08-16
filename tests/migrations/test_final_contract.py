"""Verify ORM and PostgreSQL independently against the literal final contract."""

import re

from app.core.database import Base
from tests.migrations.final_contract import FINAL_INDEX_DETAILS, FINAL_SCHEMA_CONTRACT
from tests.migrations.support import get_schema_contract, run_alembic


def _type_name(value: object) -> str:
    name = str(value).upper()
    return "TIMESTAMP" if name == "DATETIME" else name


def _default_semantics(value: object) -> str | None:
    if value is None:
        return None
    normalized = re.sub(
        r"::(?:character varying|text|boolean|integer|jsonb)",
        "",
        str(value),
        flags=re.IGNORECASE,
    ).strip()
    canonical = {
        "gen_random_uuid()": "uuid",
        "now()": "now",
        "'[]'": "json_empty",
        "'Asia/Shanghai'": "Asia/Shanghai",
        "'deny'": "deny",
        "'any'": "any",
        "true": "true",
        "false": "false",
        "0": "0",
        "1": "1",
    }
    try:
        return canonical[normalized]
    except KeyError as error:
        raise AssertionError(f"unrecognised server default: {value!r}") from error


def _normalized_check(value: str) -> str:
    """Ignore only PostgreSQL's redundant casts and whitespace."""
    without_casts = re.sub(
        r"::(?:character varying|text|boolean|integer|jsonb)(?:\[\])?",
        "",
        value,
        flags=re.IGNORECASE,
    )
    normalized = re.sub(r"\s+", " ", without_casts).strip()
    return re.sub(r" OR \((.+)\)$", r" OR \1", normalized)


def _actual_indexes(
    indexes: dict[str, dict[str, object]],
    unique_constraints: dict[str, tuple[str, ...]],
):
    return {
        name: facts for name, facts in indexes.items() if name not in unique_constraints
    }


def _orm_index(index):
    sorting = {}
    for expression in index.expressions:
        if " DESC" in str(expression).upper():
            name = getattr(getattr(expression, "element", None), "name", None)
            assert isinstance(name, str)
            sorting[name] = ("desc",)
    predicate = index.dialect_options["postgresql"].get("where")
    columns = tuple(column.name for column in index.columns)
    if index.name == "idx_short_links_name_trgm":
        columns = (None,)
    return (
        columns,
        index.unique,
        sorting,
        str(predicate) if predicate is not None else None,
    )


def _assert_contract_columns(actual, expected):
    assert set(actual) == set(expected)
    for name, definition in expected.items():
        facts = actual[name]
        assert _type_name(facts["type"]) == definition["type"]
        assert facts["nullable"] is definition["nullable"]
        assert facts.get("timezone") == definition["timezone"]
        assert _default_semantics(facts.get("default")) == definition["default"]


def test_orm_matches_the_independent_literal_final_contract():
    assert set(Base.metadata.tables) == set(FINAL_SCHEMA_CONTRACT)
    for table_name, expected in FINAL_SCHEMA_CONTRACT.items():
        table = Base.metadata.tables[table_name]
        columns = {
            column.name: {
                "type": column.type,
                "nullable": column.nullable,
                "timezone": getattr(column.type, "timezone", None),
                "default": getattr(column.server_default, "arg", None),
            }
            for column in table.columns
        }
        _assert_contract_columns(columns, expected["columns"])
        checks = {
            constraint.name: str(constraint.sqltext)
            for constraint in table.constraints
            if constraint.name and constraint.name.startswith("ck_")
        }
        assert {name: _normalized_check(value) for name, value in checks.items()} == {
            name: _normalized_check(value) for name, value in expected["checks"].items()
        }
        indexes = {index.name: _orm_index(index) for index in table.indexes}
        assert indexes == expected["indexes"]
        unique_constraints = {
            constraint.name: tuple(column.name for column in constraint.columns)
            for constraint in table.constraints
            if constraint.__class__.__name__ == "UniqueConstraint"
        }
        assert unique_constraints == expected["unique_constraints"]
        foreign_keys = {
            constraint.name: (
                tuple(element.parent.name for element in constraint.elements),
                constraint.referred_table.name,
                tuple(element.column.name for element in constraint.elements),
                constraint.elements[0].ondelete,
            )
            for constraint in table.foreign_key_constraints
        }
        assert foreign_keys == expected["foreign_keys"]


def test_postgresql_matches_the_independent_literal_final_contract(
    migration_database_url,
):
    run_alembic(migration_database_url, "upgrade", "head")
    actual = get_schema_contract(migration_database_url)
    assert actual["tables"] - {"alembic_version"} == set(FINAL_SCHEMA_CONTRACT)
    for table_name, expected in FINAL_SCHEMA_CONTRACT.items():
        _assert_contract_columns(actual["columns"][table_name], expected["columns"])
        assert {
            name: _normalized_check(value)
            for name, value in actual["checks"][table_name].items()
        } == {
            name: _normalized_check(value) for name, value in expected["checks"].items()
        }
        indexes = _actual_indexes(
            actual["indexes"][table_name], expected["unique_constraints"]
        )
        assert {
            name: {
                key: facts[key]
                for key in ("columns", "unique", "sorting", "predicate")
            }
            for name, facts in indexes.items()
        } == {
            name: {
                "columns": columns,
                "unique": unique,
                "sorting": sorting,
                "predicate": predicate,
            }
            for name, (columns, unique, sorting, predicate) in expected[
                "indexes"
            ].items()
        }
        unique_constraints = {
            name: tuple(actual["indexes"][table_name][name]["columns"])
            for name in expected["unique_constraints"]
        }
        assert unique_constraints == expected["unique_constraints"]
        assert actual["foreign_keys"][table_name] == {
            name: {
                "columns": columns,
                "referred_table": table,
                "referred_columns": referred_columns,
                "ondelete": action,
            }
            for name, (columns, table, referred_columns, action) in expected[
                "foreign_keys"
            ].items()
        }
    for table_name, details in FINAL_INDEX_DETAILS.items():
        for index_name, (using, expressions, operator_classes) in details.items():
            actual_index = actual["indexes"][table_name][index_name]
            assert actual_index["using"] == using
            assert actual_index["expressions"] == expressions
            assert actual_index["operator_classes"] == operator_classes
    assert "pg_trgm" in actual["extensions"]
