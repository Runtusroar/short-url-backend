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
from tests.migrations.final_contract import FINAL_SCHEMA_CONTRACT


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
        table_name: set(contract["indexes"])
        for table_name, contract in FINAL_SCHEMA_CONTRACT.items()
        if contract["indexes"]
    }
    expected_checks = {
        table_name: set(contract["checks"])
        for table_name, contract in FINAL_SCHEMA_CONTRACT.items()
        if contract["checks"]
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
