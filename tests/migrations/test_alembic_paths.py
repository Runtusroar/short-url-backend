from tests.migrations.support import (
    get_foreign_keys,  # noqa: F401 - part of the migration helper contract
    get_indexes,
    run_alembic,
    target_url_ondelete,
)

EXPECTED_INDEXES = {
    "short_links": {"idx_short_links_domain": ("domain_id",)},
    "access_logs": {
        "idx_access_logs_domain": ("domain_id",),
        "idx_access_logs_short_link": ("short_link_id",),
        "idx_access_logs_plus8": ("short_link_id", "accessed_at_plus8"),
        "idx_access_logs_dedup": ("short_link_id", "ip", "dedup_bucket"),
    },
}


def test_empty_database_upgrades_to_repaired_head(migration_database_url):
    run_alembic(migration_database_url, "upgrade", "head")
    assert get_indexes(migration_database_url, "short_links") == EXPECTED_INDEXES[
        "short_links"
    ]
    assert get_indexes(migration_database_url, "access_logs") == EXPECTED_INDEXES[
        "access_logs"
    ]
    assert target_url_ondelete(migration_database_url) == "SET NULL"


def test_existing_old_head_upgrades_to_repaired_head(migration_database_url):
    run_alembic(migration_database_url, "upgrade", "a9e56b03bf5f")
    assert get_indexes(migration_database_url, "short_links") == {}
    assert get_indexes(migration_database_url, "access_logs") == {}
    run_alembic(migration_database_url, "upgrade", "head")
    assert get_indexes(migration_database_url, "short_links") == EXPECTED_INDEXES[
        "short_links"
    ]
    assert get_indexes(migration_database_url, "access_logs") == EXPECTED_INDEXES[
        "access_logs"
    ]


def test_repair_revision_round_trip(migration_database_url):
    run_alembic(migration_database_url, "upgrade", "head")
    run_alembic(migration_database_url, "downgrade", "a9e56b03bf5f")
    assert get_indexes(migration_database_url, "short_links") == {}
    assert get_indexes(migration_database_url, "access_logs") == {}
    run_alembic(migration_database_url, "upgrade", "head")
    assert get_indexes(migration_database_url, "access_logs") == EXPECTED_INDEXES[
        "access_logs"
    ]


def test_alembic_check_has_no_pending_operations(migration_database_url):
    run_alembic(migration_database_url, "upgrade", "head")
    result = run_alembic(migration_database_url, "check")
    assert "No new upgrade operations detected" in result.stdout
