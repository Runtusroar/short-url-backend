from importlib import import_module

from app.core.database import Base
from app.models import (
    AccessLog,
    AccessRule,
    Domain,
    IpBlacklist,
    ShortLink,
    ShortLinkPermission,
    TargetUrl,
    User,
    UserDomain,
)


def test_models_live_in_focused_modules_and_are_reexported():
    expected = {
        "user": User,
        "domain": Domain,
        "short_link": ShortLink,
        "access_rule": AccessRule,
        "blacklist": IpBlacklist,
        "access_log": AccessLog,
    }
    for module_name, model in expected.items():
        assert (
            getattr(import_module(f"app.models.{module_name}"), model.__name__) is model
        )


def test_table_names_are_unchanged():
    assert set(Base.metadata.tables) == {
        "users",
        "domains",
        "user_domains",
        "short_links",
        "short_link_permissions",
        "target_urls",
        "access_rules",
        "ip_blacklist",
        "access_logs",
    }
    assert ShortLinkPermission.__tablename__ == "short_link_permissions"
    assert TargetUrl.__tablename__ == "target_urls"
    assert UserDomain.__tablename__ == "user_domains"


def test_final_orm_schema_declares_named_checks_indexes_and_delete_actions():
    expected_indexes = {
        "domains": {"uq_domains_one_default"},
        "user_domains": {"idx_user_domains_domain"},
        "short_links": {
            "idx_short_links_domain_created_at",
            "idx_short_links_domain_owner_created_at",
        },
        "access_rules": {"idx_access_rules_link_active_priority"},
        "ip_blacklist": {"idx_ip_blacklist_active_expires_at"},
        "access_logs": {
            "idx_access_logs_domain_accessed_at",
            "idx_access_logs_link_accessed_at",
            "idx_access_logs_link_access_date",
            "idx_access_logs_link_client_ip_dedup",
            "idx_access_logs_domain_result_accessed_at",
            "idx_access_logs_domain_country_accessed_at",
        },
    }
    expected_checks = {
        "users": {"ck_users_role", "ck_users_username_lower"},
        "domains": {"ck_domains_name_lower_host"},
        "short_links": {
            "ck_short_links_default_action",
            "ck_short_links_deleted_actor",
        },
        "target_urls": {"ck_target_urls_type", "ck_target_urls_weight"},
        "access_rules": {
            "ck_access_rules_action",
            "ck_access_rules_client_requirement",
            "ck_access_rules_proxy_requirement",
            "ck_access_rules_countries_array",
            "ck_access_rules_ua_platforms_array",
            "ck_access_rules_referer_patterns_array",
        },
        "ip_blacklist": {"ck_ip_blacklist_reason", "ck_ip_blacklist_removed_actor"},
        "access_logs": {
            "ck_access_logs_result",
            "ck_access_logs_country",
            "ck_access_logs_decision_reason",
            "ck_access_logs_request_method",
            "ck_access_logs_proxy_check_status",
            "ck_access_logs_proxy_source",
            "ck_access_logs_proxy_types_array",
        },
    }
    for table_name, names in expected_indexes.items():
        assert {
            index.name for index in Base.metadata.tables[table_name].indexes
        } == names
    assert "idx_short_links_domain" not in {
        index.name for index in Base.metadata.tables["short_links"].indexes
    }
    assert {
        "idx_access_logs_domain",
        "idx_access_logs_short_link",
        "idx_access_logs_plus8",
        "idx_access_logs_dedup",
    }.isdisjoint({index.name for index in Base.metadata.tables["access_logs"].indexes})
    for table_name, names in expected_checks.items():
        constraints = Base.metadata.tables[table_name].constraints
        assert {
            constraint.name for constraint in constraints if constraint.name
        } >= names

    expected_actions = {
        "access_logs": {
            "short_link_id": "RESTRICT",
            "domain_id": "RESTRICT",
            "target_url_id": "SET NULL",
            "matched_rule_id": "SET NULL",
        },
        "short_links": {
            "domain_id": "RESTRICT",
            "owner_id": "RESTRICT",
            "deleted_by": "RESTRICT",
        },
        "short_link_permissions": {
            "short_link_id": "RESTRICT",
            "user_id": "RESTRICT",
            "granted_by": "RESTRICT",
        },
        "user_domains": {
            "user_id": "RESTRICT",
            "domain_id": "RESTRICT",
            "granted_by": "RESTRICT",
        },
        "ip_blacklist": {"created_by": "RESTRICT", "removed_by": "RESTRICT"},
        "access_rules": {"short_link_id": "CASCADE"},
        "target_urls": {"short_link_id": "CASCADE"},
    }
    for table_name, by_column in expected_actions.items():
        table = Base.metadata.tables[table_name]
        actual = {
            foreign_key.parent.name: foreign_key.ondelete
            for foreign_key in table.foreign_key_constraints
            for foreign_key in foreign_key.elements
        }
        assert actual == by_column
