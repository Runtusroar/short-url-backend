from app.db.models import AccessLog, UserDomainAccess


def test_domain_access_has_composite_primary_key():
    """A surrogate key would allow duplicate or ambiguous domain grants."""
    assert {column.name for column in UserDomainAccess.__table__.primary_key} == {
        "user_id",
        "domain_id",
    }


def test_access_log_foreign_keys_set_null():
    """Deleting mutable records must preserve historical access snapshots."""
    actions = {
        foreign_key.ondelete
        for column in (AccessLog.short_link_id, AccessLog.domain_id, AccessLog.target_url_id)
        for foreign_key in column.property.columns[0].foreign_keys
    }
    assert actions == {"SET NULL"}


def test_access_log_indexes_match_the_cursor_and_filter_queries():
    """Ascending or missing compound indexes would regress log searches."""
    indexes = {index.name: index for index in AccessLog.__table__.indexes}

    assert set(indexes) == {
        "ix_access_logs_accessed_at_id",
        "ix_access_logs_domain_accessed_at_id",
        "ix_access_logs_short_link_accessed_at_id",
        "ix_access_logs_result_accessed_at",
        "ix_access_logs_country_code_accessed_at",
        "ix_access_logs_block_reason_accessed_at",
    }
    assert str(indexes["ix_access_logs_accessed_at_id"].expressions[0]) == "accessed_at DESC"
