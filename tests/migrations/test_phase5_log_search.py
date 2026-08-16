"""Phase 5 canonical short-code and access-log index migration contracts."""

from subprocess import CalledProcessError
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, inspect, text

from tests.migrations.support import get_schema_contract, run_alembic

HEAD = "c8e4f1a26b73"
PREVIOUS = "a73f0b9d4216"


def _seed_user_domain(connection, owner_id: UUID, domain_id: UUID) -> None:
    connection.execute(
        text(
            "INSERT INTO users "
            "(id, username, password_hash, role, is_active, created_at, updated_at) "
            "VALUES (:id, :username, 'hash', 'admin', true, now(), now())"
        ),
        {"id": owner_id, "username": f"phase5-{owner_id.hex[:16]}"},
    )
    connection.execute(
        text(
            "INSERT INTO domains "
            "(id, name, is_active, is_default, created_at) "
            "VALUES (:id, :name, true, false, now())"
        ),
        {"id": domain_id, "name": f"phase5-{domain_id.hex[:16]}.example.test"},
    )


def _scalar(database_url: str, statement: str) -> object:
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            return connection.scalar(text(statement))
    finally:
        engine.dispose()


def _index_fact(
    columns: tuple[str | None, ...], sorting: dict[str, tuple[str, ...]]
) -> dict[str, object]:
    return {
        "columns": columns,
        "unique": False,
        "sorting": sorting,
        "predicate": None,
        "using": "btree",
        "expressions": (),
        "operator_classes": tuple("" for _ in columns),
    }


def test_phase5_upgrade_canonicalizes_codes_and_builds_search_contract(
    migration_database_url,
):
    run_alembic(migration_database_url, "upgrade", PREVIOUS)
    engine = create_engine(migration_database_url)
    domain_id, owner_id, link_id = uuid4(), uuid4(), uuid4()
    try:
        with engine.begin() as connection:
            _seed_user_domain(connection, owner_id, domain_id)
            connection.execute(
                text(
                    "INSERT INTO short_links "
                    "(id, domain_id, short_code, is_custom_alias, name, owner_id, "
                    "is_active, default_action, created_at, updated_at) "
                    "VALUES (:id, :domain, ' Promo7 ', true, 'August Promotion', "
                    ":owner, true, 'deny', now(), now())"
                ),
                {"id": link_id, "domain": domain_id, "owner": owner_id},
            )
    finally:
        engine.dispose()

    run_alembic(migration_database_url, "upgrade", HEAD)
    contract = get_schema_contract(migration_database_url)
    assert _scalar(migration_database_url, "SELECT short_code FROM short_links") == "promo7"
    assert "ck_short_links_short_code_canonical" in contract["checks"]["short_links"]
    assert contract["extensions"] >= {"pg_trgm"}
    assert contract["indexes"]["short_links"]["idx_short_links_name_trgm"] == {
        "columns": (None,),
        "unique": False,
        "sorting": {},
        "predicate": None,
        "using": "gin",
        "expressions": ("lower((name)::text)",),
        "operator_classes": ("gin_trgm_ops",),
    }
    assert contract["indexes"]["short_links"]["idx_short_links_domain_code_pattern"] == _index_fact(
        ("domain_id", "short_code"), {}
    ) | {"operator_classes": ("", "varchar_pattern_ops")}
    assert contract["indexes"]["access_logs"]["idx_access_logs_domain_accessed_at"] == _index_fact(
        ("domain_id", "accessed_at", "id"),
        {"accessed_at": ("desc",), "id": ("desc",)},
    )
    assert contract["indexes"]["access_logs"]["idx_access_logs_link_accessed_at"] == _index_fact(
        ("short_link_id", "accessed_at", "id"),
        {"accessed_at": ("desc",), "id": ("desc",)},
    )
    assert contract["indexes"]["access_logs"]["idx_access_logs_domain_result_accessed_at"] == _index_fact(
        ("domain_id", "result", "accessed_at", "id"),
        {"accessed_at": ("desc",), "id": ("desc",)},
    )
    assert contract["indexes"]["access_logs"]["idx_access_logs_domain_country_accessed_at"] == _index_fact(
        ("domain_id", "country", "accessed_at", "id"),
        {"accessed_at": ("desc",), "id": ("desc",)},
    )


def seed_links_with_codes(database_url: str, codes: tuple[str, ...]) -> None:
    engine = create_engine(database_url)
    try:
        with engine.begin() as connection:
            owner_id, domain_id = uuid4(), uuid4()
            _seed_user_domain(connection, owner_id, domain_id)
            for code in codes:
                connection.execute(
                    text(
                        "INSERT INTO short_links "
                        "(id, domain_id, short_code, is_custom_alias, name, owner_id, "
                        "is_active, default_action, created_at, updated_at) "
                        "VALUES (:id, :domain_id, :code, true, :name, :owner_id, "
                        "true, 'deny', now(), now())"
                    ),
                    {
                        "id": uuid4(),
                        "domain_id": domain_id,
                        "code": code,
                        "name": f"seed {code}",
                        "owner_id": owner_id,
                    },
                )
    finally:
        engine.dispose()


def current_revision(database_url: str) -> str:
    value = _scalar(database_url, "SELECT version_num FROM alembic_version")
    assert isinstance(value, str)
    return value


def original_codes(database_url: str) -> list[str]:
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            return list(connection.scalars(text("SELECT short_code FROM short_links ORDER BY id")))
    finally:
        engine.dispose()


def has_check(database_url: str, name: str) -> bool:
    engine = create_engine(database_url)
    try:
        return any(
            check.get("name") == name
            for check in inspect(engine).get_check_constraints("short_links")
        )
    finally:
        engine.dispose()


def has_index(database_url: str, name: str) -> bool:
    engine = create_engine(database_url)
    try:
        return any(index.get("name") == name for index in inspect(engine).get_indexes("short_links"))
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    ("codes", "invariant"),
    [
        (("Promo", "promo"), "short code normalization collision invariant"),
        (("bad code!",), "short code canonicalization invariant"),
        (("ab",), "short code canonicalization invariant"),
    ],
)
def test_short_code_preflight_rejects_before_any_write(
    migration_database_url, codes, invariant
):
    run_alembic(migration_database_url, "upgrade", PREVIOUS)
    seed_links_with_codes(migration_database_url, codes)
    with pytest.raises(CalledProcessError) as exc_info:
        run_alembic(migration_database_url, "upgrade", HEAD)
    assert invariant in f"{exc_info.value.stdout}\n{exc_info.value.stderr}"
    assert current_revision(migration_database_url) == PREVIOUS
    assert sorted(original_codes(migration_database_url)) == sorted(codes)
    assert not has_check(migration_database_url, "ck_short_links_short_code_canonical")
    assert not has_index(migration_database_url, "idx_short_links_name_trgm")


def test_phase5_downgrade_restores_representable_predecessor_indexes(
    migration_database_url,
):
    run_alembic(migration_database_url, "upgrade", PREVIOUS)
    owner_id, domain_id, link_id = uuid4(), uuid4(), uuid4()
    engine = create_engine(migration_database_url)
    try:
        with engine.begin() as connection:
            _seed_user_domain(connection, owner_id, domain_id)
            connection.execute(
                text(
                    "INSERT INTO short_links "
                    "(id, domain_id, short_code, is_custom_alias, name, owner_id, "
                    "is_active, default_action, created_at, updated_at) "
                    "VALUES (:id, :domain_id, ' Downgrade7 ', true, 'downgrade', "
                    ":owner_id, true, 'deny', now(), now())"
                ),
                {"id": link_id, "domain_id": domain_id, "owner_id": owner_id},
            )
    finally:
        engine.dispose()
    run_alembic(migration_database_url, "upgrade", HEAD)
    run_alembic(migration_database_url, "downgrade", PREVIOUS)
    contract = get_schema_contract(migration_database_url)
    assert "ck_short_links_short_code_canonical" not in contract["checks"]["short_links"]
    assert "idx_short_links_name_trgm" not in contract["indexes"]["short_links"]
    assert "idx_short_links_domain_code_pattern" not in contract["indexes"]["short_links"]
    assert contract["extensions"] >= {"pg_trgm"}
    for name, columns in {
        "idx_access_logs_domain_accessed_at": ("domain_id", "accessed_at"),
        "idx_access_logs_link_accessed_at": ("short_link_id", "accessed_at"),
        "idx_access_logs_domain_result_accessed_at": (
            "domain_id", "result", "accessed_at"
        ),
        "idx_access_logs_domain_country_accessed_at": (
            "domain_id", "country", "accessed_at"
        ),
    }.items():
        assert contract["indexes"]["access_logs"][name]["columns"] == columns
        assert contract["indexes"]["access_logs"][name]["sorting"] == {
            "accessed_at": ("desc",)
        }
    engine = create_engine(migration_database_url)
    try:
        with engine.connect() as connection:
            assert connection.scalar(
                text("SELECT short_code FROM short_links WHERE id = :id"),
                {"id": link_id},
            ) == "downgrade7"
    finally:
        engine.dispose()
    run_alembic(migration_database_url, "upgrade", HEAD)
