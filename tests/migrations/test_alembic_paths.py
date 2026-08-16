from tests.migrations.support import (
    get_foreign_keys,
    get_indexes,
    run_alembic,
    target_url_ondelete,
)

EXPECTED_INDEXES = {
    "short_links": {},
    "access_logs": {
        "idx_access_logs_domain_accessed_at": ("domain_id", "accessed_at"),
        "idx_access_logs_link_accessed_at": ("short_link_id", "accessed_at"),
        "idx_access_logs_link_access_date": ("short_link_id", "access_date"),
        "idx_access_logs_link_client_ip_dedup": ("short_link_id", "client_ip", "dedup_bucket"),
        "idx_access_logs_domain_result_accessed_at": ("domain_id", "result", "accessed_at"),
        "idx_access_logs_domain_country_accessed_at": ("domain_id", "country", "accessed_at"),
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
    assert get_foreign_keys(migration_database_url, "access_logs")["target_url_id"][
        "name"
    ] == "fk_access_logs_target_url"


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


def test_stamped_old_head_repairs_target_url_foreign_key_drift(
    migration_database_url,
):
    run_alembic(migration_database_url, "upgrade", "b1e5f6851085")
    assert target_url_ondelete(migration_database_url) is None

    run_alembic(migration_database_url, "stamp", "a9e56b03bf5f")
    run_alembic(migration_database_url, "upgrade", "head")

    assert target_url_ondelete(migration_database_url) == "SET NULL"
    assert get_foreign_keys(migration_database_url, "access_logs")["target_url_id"][
        "name"
    ] == "fk_access_logs_target_url"
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


def test_repaired_head_downgrades_through_historical_fk_revision(
    migration_database_url,
):
    run_alembic(migration_database_url, "upgrade", "head")
    run_alembic(migration_database_url, "downgrade", "b1e5f6851085")

    assert target_url_ondelete(migration_database_url) is None


def test_alembic_check_has_no_pending_operations(migration_database_url):
    run_alembic(migration_database_url, "upgrade", "head")
    result = run_alembic(migration_database_url, "check")
    assert "No new upgrade operations detected" in result.stdout
