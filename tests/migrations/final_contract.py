"""Independent literal Phase 5 PostgreSQL contract.

This module intentionally imports no application model, migration, or schema
checker.  Constraint-backed unique indexes are recorded in ``unique_constraints``
rather than ``indexes`` because PostgreSQL exposes them through index reflection.
"""


def column(type_name, nullable, default=None, timezone=None):
    return {
        "type": type_name,
        "nullable": nullable,
        "default": default,
        "timezone": timezone,
    }


FINAL_ORM_INDEX_COLUMNS = {
    "short_links": {
        "idx_short_links_domain_created_at": ("domain_id", "created_at"),
        "idx_short_links_domain_owner_created_at": (
            "domain_id",
            "owner_id",
            "created_at",
        ),
        "idx_short_links_name_trgm": ("name",),
        "idx_short_links_domain_code_pattern": ("domain_id", "short_code"),
    },
    "access_logs": {
        "idx_access_logs_domain_accessed_at": ("domain_id", "accessed_at", "id"),
        "idx_access_logs_link_accessed_at": (
            "short_link_id",
            "accessed_at",
            "id",
        ),
        "idx_access_logs_link_access_date": ("short_link_id", "access_date"),
        "idx_access_logs_link_client_ip_dedup": (
            "short_link_id",
            "client_ip",
            "dedup_bucket",
        ),
        "idx_access_logs_domain_result_accessed_at": (
            "domain_id",
            "result",
            "accessed_at",
            "id",
        ),
        "idx_access_logs_domain_country_accessed_at": (
            "domain_id",
            "country",
            "accessed_at",
            "id",
        ),
    },
}


FINAL_SCHEMA_CONTRACT = {
    "users": {
        "columns": {
            "id": column("UUID", False, "uuid"),
            "username": column("VARCHAR(64)", False),
            "password_hash": column("VARCHAR(255)", False),
            "role": column("VARCHAR(16)", False),
            "is_active": column("BOOLEAN", False, "true"),
            "created_at": column("TIMESTAMP", False, "now", True),
            "updated_at": column("TIMESTAMP", False, "now", True),
        },
        "checks": {
            "ck_users_role": "role = ANY (ARRAY['admin', 'operator', 'client'])",
            "ck_users_username_lower": "username = lower(btrim(username))",
        },
        "indexes": {},
        "unique_constraints": {"users_username_key": ("username",)},
        "foreign_keys": {},
    },
    "domains": {
        "columns": {
            "id": column("UUID", False, "uuid"),
            "name": column("VARCHAR(255)", False),
            "timezone": column("VARCHAR(64)", False, "Asia/Shanghai"),
            "is_active": column("BOOLEAN", False, "true"),
            "is_default": column("BOOLEAN", False, "false"),
            "created_at": column("TIMESTAMP", False, "now", True),
            "updated_at": column("TIMESTAMP", False, "now", True),
        },
        "checks": {
            "ck_domains_name_lower_host": "name = lower(btrim(name)) AND length(name) <= 253 AND name ~ '^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)*$'"
        },
        "indexes": {
            "uq_domains_one_default": (("is_default",), True, {}, "is_default")
        },
        "unique_constraints": {"domains_name_key": ("name",)},
        "foreign_keys": {},
    },
    "user_domains": {
        "columns": {
            "id": column("UUID", False, "uuid"),
            "user_id": column("UUID", False),
            "domain_id": column("UUID", False),
            "granted_by": column("UUID", True),
            "created_at": column("TIMESTAMP", False, "now", True),
        },
        "checks": {},
        "indexes": {"idx_user_domains_domain": (("domain_id",), False, {}, None)},
        "unique_constraints": {"uq_user_domain": ("user_id", "domain_id")},
        "foreign_keys": {
            "fk_user_domains_user": (("user_id",), "users", ("id",), "RESTRICT"),
            "fk_user_domains_domain": (("domain_id",), "domains", ("id",), "RESTRICT"),
            "fk_user_domains_granted_by": (
                ("granted_by",),
                "users",
                ("id",),
                "RESTRICT",
            ),
        },
    },
    "short_links": {
        "columns": {
            "id": column("UUID", False, "uuid"),
            "domain_id": column("UUID", False),
            "short_code": column("VARCHAR(32)", False),
            "is_custom_alias": column("BOOLEAN", False, "false"),
            "name": column("VARCHAR(128)", False),
            "owner_id": column("UUID", False),
            "is_active": column("BOOLEAN", False, "true"),
            "default_action": column("VARCHAR(16)", False, "deny"),
            "deleted_at": column("TIMESTAMP", True, timezone=True),
            "deleted_by": column("UUID", True),
            "created_at": column("TIMESTAMP", False, "now", True),
            "updated_at": column("TIMESTAMP", False, "now", True),
        },
        "checks": {
            "ck_short_links_default_action": "default_action = ANY (ARRAY['allow', 'deny'])",
            "ck_short_links_deleted_actor": "deleted_by IS NULL OR deleted_at IS NOT NULL",
            "ck_short_links_short_code_canonical": "short_code = lower(btrim(short_code)) AND short_code ~ '^[a-z0-9_-]{3,32}$'",
        },
        "indexes": {
            "idx_short_links_domain_created_at": (
                ("domain_id", "created_at"),
                False,
                {"created_at": ("desc",)},
                None,
            ),
            "idx_short_links_domain_owner_created_at": (
                ("domain_id", "owner_id", "created_at"),
                False,
                {"created_at": ("desc",)},
                None,
            ),
            "idx_short_links_name_trgm": ((None,), False, {}, None),
            "idx_short_links_domain_code_pattern": (
                ("domain_id", "short_code"),
                False,
                {},
                None,
            ),
        },
        "unique_constraints": {"uq_domain_short_code": ("domain_id", "short_code")},
        "foreign_keys": {
            "fk_short_links_domain": (("domain_id",), "domains", ("id",), "RESTRICT"),
            "fk_short_links_owner": (("owner_id",), "users", ("id",), "RESTRICT"),
            "fk_short_links_deleted_by": (
                ("deleted_by",),
                "users",
                ("id",),
                "RESTRICT",
            ),
        },
    },
    "short_link_permissions": {
        "columns": {
            "id": column("UUID", False, "uuid"),
            "short_link_id": column("UUID", False),
            "user_id": column("UUID", False),
            "granted_by": column("UUID", True),
            "created_at": column("TIMESTAMP", False, "now", True),
        },
        "checks": {},
        "indexes": {},
        "unique_constraints": {"uq_short_link_user": ("short_link_id", "user_id")},
        "foreign_keys": {
            "fk_short_link_permissions_link": (
                ("short_link_id",),
                "short_links",
                ("id",),
                "RESTRICT",
            ),
            "fk_short_link_permissions_user": (
                ("user_id",),
                "users",
                ("id",),
                "RESTRICT",
            ),
            "fk_short_link_permissions_granted_by": (
                ("granted_by",),
                "users",
                ("id",),
                "RESTRICT",
            ),
        },
    },
    "target_urls": {
        "columns": {
            "id": column("UUID", False, "uuid"),
            "short_link_id": column("UUID", False),
            "name": column("VARCHAR(128)", True),
            "url": column("TEXT", False),
            "url_type": column("VARCHAR(16)", False),
            "weight": column("INTEGER", False, "1"),
            "is_active": column("BOOLEAN", False, "true"),
            "created_at": column("TIMESTAMP", False, "now", True),
            "updated_at": column("TIMESTAMP", False, "now", True),
        },
        "checks": {
            "ck_target_urls_type": "url_type = ANY (ARRAY['allowed', 'denied'])",
            "ck_target_urls_weight": "weight >= 1",
        },
        "indexes": {},
        "unique_constraints": {},
        "foreign_keys": {
            "target_urls_short_link_id_fkey": (
                ("short_link_id",),
                "short_links",
                ("id",),
                "CASCADE",
            )
        },
    },
    "access_rules": {
        "columns": {
            "id": column("UUID", False, "uuid"),
            "short_link_id": column("UUID", False),
            "name": column("VARCHAR(128)", False),
            "action": column("VARCHAR(16)", False),
            "priority": column("INTEGER", False, "0"),
            "countries": column("JSONB", False, "json_empty"),
            "ua_platforms": column("JSONB", False, "json_empty"),
            "referer_patterns": column("JSONB", False, "json_empty"),
            "client_requirement": column("VARCHAR(16)", False, "any"),
            "proxy_requirement": column("VARCHAR(16)", False, "any"),
            "is_active": column("BOOLEAN", False, "true"),
            "created_at": column("TIMESTAMP", False, "now", True),
            "updated_at": column("TIMESTAMP", False, "now", True),
        },
        "checks": {
            "ck_access_rules_action": "action = ANY (ARRAY['allow', 'deny'])",
            "ck_access_rules_client_requirement": "client_requirement = ANY (ARRAY['any', 'human', 'bot'])",
            "ck_access_rules_proxy_requirement": "proxy_requirement = ANY (ARRAY['any', 'non_proxy', 'proxy'])",
            "ck_access_rules_countries_array": "jsonb_typeof(countries) = 'array'",
            "ck_access_rules_ua_platforms_array": "jsonb_typeof(ua_platforms) = 'array'",
            "ck_access_rules_referer_patterns_array": "jsonb_typeof(referer_patterns) = 'array'",
        },
        "indexes": {
            "idx_access_rules_link_active_priority": (
                ("short_link_id", "priority", "id"),
                False,
                {},
                "is_active",
            )
        },
        "unique_constraints": {
            "uq_access_rules_link_priority": ("short_link_id", "priority")
        },
        "foreign_keys": {
            "fk_access_rules_short_link": (
                ("short_link_id",),
                "short_links",
                ("id",),
                "CASCADE",
            )
        },
    },
    "ip_blacklist": {
        "columns": {
            "id": column("UUID", False, "uuid"),
            "ip": column("INET", False),
            "reason": column("TEXT", False),
            "expires_at": column("TIMESTAMP", True, timezone=True),
            "created_by": column("UUID", True),
            "created_at": column("TIMESTAMP", False, "now", True),
            "removed_at": column("TIMESTAMP", True, timezone=True),
            "removed_by": column("UUID", True),
            "removal_reason": column("TEXT", True),
        },
        "checks": {
            "ck_ip_blacklist_reason": "btrim(reason) <> ''",
            "ck_ip_blacklist_removed_actor": "removed_by IS NULL OR removed_at IS NOT NULL",
        },
        "indexes": {
            "idx_ip_blacklist_active_expires_at": (
                ("expires_at",),
                False,
                {},
                "removed_at IS NULL",
            )
        },
        "unique_constraints": {"ip_blacklist_ip_key": ("ip",)},
        "foreign_keys": {
            "fk_ip_blacklist_created_by": (
                ("created_by",),
                "users",
                ("id",),
                "RESTRICT",
            ),
            "fk_ip_blacklist_removed_by": (
                ("removed_by",),
                "users",
                ("id",),
                "RESTRICT",
            ),
        },
    },
    "access_logs": {
        "columns": {
            "id": column("UUID", False, "uuid"),
            "short_link_id": column("UUID", False),
            "domain_id": column("UUID", False),
            "target_url_id": column("UUID", True),
            "matched_rule_id": column("UUID", True),
            "result": column("VARCHAR(16)", False),
            "client_ip": column("INET", True),
            "country": column("CHAR(2)", True),
            "user_agent": column("TEXT", True),
            "ua_platform": column("VARCHAR(64)", True),
            "referer": column("TEXT", True),
            "accessed_at": column("TIMESTAMP", False, "now", True),
            "access_date": column("DATE", False),
            "dedup_bucket": column("BIGINT", False),
            "decision_reason": column("VARCHAR(16)", False),
            "matched_rule_name": column("VARCHAR(128)", True),
            "target_url_snapshot": column("TEXT", True),
            "request_host": column("VARCHAR(255)", True),
            "request_method": column("VARCHAR(8)", False),
            "proxy_check_status": column("VARCHAR(16)", False),
            "is_anonymous": column("BOOLEAN", True),
            "proxy_types": column("JSONB", False, "json_empty"),
            "proxy_source": column("VARCHAR(32)", True),
            "proxy_error_code": column("VARCHAR(32)", True),
        },
        "checks": {
            "ck_access_logs_result": "result = ANY (ARRAY['allowed', 'denied', 'blocked'])",
            "ck_access_logs_country": "country IS NULL OR country ~ '^[A-Z]{2}$'",
            "ck_access_logs_decision_reason": "decision_reason = ANY (ARRAY['legacy_unknown', 'blacklist', 'matched_rule', 'default_action', 'no_target'])",
            "ck_access_logs_request_method": "request_method = ANY (ARRAY['GET', 'HEAD'])",
            "ck_access_logs_proxy_check_status": "proxy_check_status = ANY (ARRAY['skipped', 'cached', 'checked', 'assumed_bot', 'error'])",
            "ck_access_logs_proxy_source": "proxy_source IS NULL OR (proxy_source = ANY (ARRAY['maxmind_insights', 'assumed_bot']))",
            "ck_access_logs_proxy_types_array": "jsonb_typeof(proxy_types) = 'array'",
            "ck_access_logs_proxy_error_code": "proxy_error_code IS NULL OR proxy_error_code = ANY (ARRAY['disabled', 'redis_unavailable', 'auth_failed', 'insufficient_funds', 'permission_denied', 'rate_limited', 'timeout', 'upstream_error', 'invalid_response', 'ip_not_found', 'invalid_ip', 'non_global_ip', 'lookup_contended'])",
        },
        "indexes": {
            "idx_access_logs_domain_accessed_at": (
                ("domain_id", "accessed_at", "id"),
                False,
                {"accessed_at": ("desc",), "id": ("desc",)},
                None,
            ),
            "idx_access_logs_link_accessed_at": (
                ("short_link_id", "accessed_at", "id"),
                False,
                {"accessed_at": ("desc",), "id": ("desc",)},
                None,
            ),
            "idx_access_logs_link_access_date": (
                ("short_link_id", "access_date"),
                False,
                {"access_date": ("desc",)},
                None,
            ),
            "idx_access_logs_link_client_ip_dedup": (
                ("short_link_id", "client_ip", "dedup_bucket"),
                False,
                {},
                None,
            ),
            "idx_access_logs_domain_result_accessed_at": (
                ("domain_id", "result", "accessed_at", "id"),
                False,
                {"accessed_at": ("desc",), "id": ("desc",)},
                None,
            ),
            "idx_access_logs_domain_country_accessed_at": (
                ("domain_id", "country", "accessed_at", "id"),
                False,
                {"accessed_at": ("desc",), "id": ("desc",)},
                None,
            ),
        },
        "unique_constraints": {},
        "foreign_keys": {
            "fk_access_logs_short_link": (
                ("short_link_id",),
                "short_links",
                ("id",),
                "RESTRICT",
            ),
            "fk_access_logs_domain": (("domain_id",), "domains", ("id",), "RESTRICT"),
            "fk_access_logs_target_url": (
                ("target_url_id",),
                "target_urls",
                ("id",),
                "SET NULL",
            ),
            "fk_access_logs_matched_rule": (
                ("matched_rule_id",),
                "access_rules",
                ("id",),
                "SET NULL",
            ),
        },
    },
}

FINAL_INDEX_DETAILS = {
    "short_links": {
        "idx_short_links_name_trgm": (
            "gin",
            ("lower((name)::text)",),
            ("gin_trgm_ops",),
        ),
        "idx_short_links_domain_code_pattern": (
            "btree",
            (),
            ("", "varchar_pattern_ops"),
        ),
    },
    "access_logs": {
        "idx_access_logs_domain_accessed_at": ("btree", (), ("", "", "")),
        "idx_access_logs_link_accessed_at": ("btree", (), ("", "", "")),
        "idx_access_logs_domain_result_accessed_at": (
            "btree",
            (),
            ("", "", "", ""),
        ),
        "idx_access_logs_domain_country_accessed_at": (
            "btree",
            (),
            ("", "", "", ""),
        ),
    },
}
