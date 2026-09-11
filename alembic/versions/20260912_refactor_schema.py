"""normalize the schema while preserving existing short-url data

Revision ID: 20260912_refactor
Revises: a9e56b03bf5f
"""

import json

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from app.services.policy_migration import convert_legacy_policy


revision = "20260912_refactor"
down_revision = "a9e56b03bf5f"
branch_labels = None
depends_on = None


def _preflight_and_copy_policies() -> None:
    bind = op.get_bind()
    links = bind.execute(sa.text("SELECT id, default_action FROM short_links ORDER BY id")).mappings()
    failures = []
    converted = []
    for link in links:
        rules = bind.execute(
            sa.text(
                """
                SELECT id, action, priority, countries, ua_platforms, referer_pattern,
                       allow_proxy, allow_bot, is_active
                FROM access_rules WHERE short_link_id = :link_id
                ORDER BY priority, id
                """
            ),
            {"link_id": link["id"]},
        ).mappings().all()
        conversion = convert_legacy_policy(dict(link), rules)
        if not conversion.convertible:
            failures.extend(
                f"{link_id}: {conversion.reason}"
                for link_id in (conversion.affected_ids or (str(link["id"]),))
            )
        elif conversion.policy is not None:
            converted.append((link["id"], conversion.policy))
    if failures:
        raise RuntimeError(
            "Cannot safely convert legacy policies for short links: " + ", ".join(failures)
        )

    for link_id, policy in converted:
        bind.execute(
            sa.text(
                """
                INSERT INTO link_policies
                    (short_link_id, country_mode, countries, platform_mode, platforms,
                     referer_mode, referer_patterns, block_proxy, block_bot, updated_at)
                VALUES
                    (:short_link_id, :country_mode, CAST(:countries AS jsonb), :platform_mode,
                     CAST(:platforms AS jsonb), :referer_mode, CAST(:referer_patterns AS jsonb),
                     :block_proxy, :block_bot, now())
                """
            ),
            {
                "short_link_id": link_id,
                "country_mode": policy.country_mode,
                "countries": json.dumps(policy.countries),
                "platform_mode": policy.platform_mode,
                "platforms": json.dumps(policy.platforms),
                "referer_mode": policy.referer_mode,
                "referer_patterns": json.dumps(policy.referer_patterns),
                "block_proxy": policy.block_proxy,
                "block_bot": policy.block_bot,
            },
        )


def upgrade() -> None:
    uuid = postgresql.UUID(as_uuid=True)
    bind = op.get_bind()
    bind.execute(
        sa.text(
            """
            CREATE OR REPLACE FUNCTION pg_temp.safe_inet(value text) RETURNS inet
            LANGUAGE plpgsql AS $$
            BEGIN
                RETURN NULLIF(btrim(value), '')::inet;
            EXCEPTION WHEN OTHERS THEN
                RETURN NULL;
            END;
            $$
            """
        )
    )
    invalid_blacklist = bind.execute(
        sa.text(
            """
            SELECT id, ip FROM ip_blacklist
            WHERE pg_temp.safe_inet(ip) IS NULL
            ORDER BY id
            """
        )
    ).mappings().all()
    if invalid_blacklist:
        values = ", ".join(f"{row['id']}={row['ip']!r}" for row in invalid_blacklist)
        raise RuntimeError(f"Cannot safely convert invalid IP blacklist entries: {values}")
    duplicate_blacklist = bind.execute(
        sa.text(
            """
            SELECT pg_temp.safe_inet(ip)::text AS ip, string_agg(id::text, ', ' ORDER BY id) AS ids
            FROM ip_blacklist
            GROUP BY pg_temp.safe_inet(ip)
            HAVING count(*) > 1
            """
        )
    ).mappings().all()
    if duplicate_blacklist:
        values = ", ".join(f"{row['ip']} ({row['ids']})" for row in duplicate_blacklist)
        raise RuntimeError(f"Cannot safely merge duplicate IP blacklist entries: {values}")

    op.create_table(
        "user_domain_access",
        sa.Column("user_id", uuid, sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("domain_id", uuid, sa.ForeignKey("domains.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("access_level", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("access_level IN ('read', 'manage')", name="ck_user_domain_access_level"),
    )
    op.create_table(
        "link_policies",
        sa.Column("short_link_id", uuid, sa.ForeignKey("short_links.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("country_mode", sa.String(16), nullable=False, server_default="off"),
        sa.Column("countries", postgresql.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("platform_mode", sa.String(16), nullable=False, server_default="off"),
        sa.Column("platforms", postgresql.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("referer_mode", sa.String(16), nullable=False, server_default="off"),
        sa.Column("referer_patterns", postgresql.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("block_proxy", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("block_bot", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("country_mode IN ('off', 'allow', 'block')", name="ck_link_policies_country_mode"),
        sa.CheckConstraint("platform_mode IN ('off', 'allow', 'block')", name="ck_link_policies_platform_mode"),
        sa.CheckConstraint("referer_mode IN ('off', 'allow', 'block')", name="ck_link_policies_referer_mode"),
    )
    op.create_table(
        "ip_reputation",
        sa.Column("ip", postgresql.INET, primary_key=True),
        sa.Column("is_proxy", sa.Boolean, nullable=False),
        sa.Column("proxy_type", sa.String(64)),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )

    bind.execute(
        sa.text(
            """
            INSERT INTO user_domain_access (user_id, domain_id, access_level, created_at)
            SELECT user_id, domain_id,
                   CASE WHEN users.role = 'client' THEN 'read' ELSE 'manage' END,
                   user_domains.created_at
            FROM user_domains JOIN users ON users.id = user_domains.user_id
            WHERE users.role != 'admin'
            ON CONFLICT (user_id, domain_id) DO NOTHING
            """
        )
    )
    bind.execute(sa.text("UPDATE users SET role = 'subaccount' WHERE role IN ('operator', 'client')"))
    op.create_check_constraint("ck_users_role", "users", "role IN ('admin', 'subaccount')")

    op.add_column("short_links", sa.Column("note", sa.Text))
    bind.execute(sa.text("UPDATE short_links SET note = description"))
    op.alter_column("short_links", "owner_id", nullable=True)
    op.drop_constraint("short_links_owner_id_fkey", "short_links", type_="foreignkey")
    op.create_foreign_key("fk_short_links_owner", "short_links", "users", ["owner_id"], ["id"], ondelete="SET NULL")
    bind.execute(sa.text("UPDATE target_urls SET url_type = 'blocked' WHERE url_type = 'denied'"))
    op.create_check_constraint("ck_target_urls_type", "target_urls", "url_type IN ('allowed', 'blocked')")
    op.create_check_constraint("ck_target_urls_weight_nonnegative", "target_urls", "weight >= 0")

    # Conversion is a hard gate before the legacy rule/default structures are removed.
    _preflight_and_copy_policies()

    op.add_column("access_logs", sa.Column("request_url", sa.Text))
    op.add_column("access_logs", sa.Column("domain_name", sa.String(255)))
    op.add_column("access_logs", sa.Column("short_code", sa.String(32)))
    op.add_column("access_logs", sa.Column("short_link_note", sa.Text))
    op.add_column("access_logs", sa.Column("target_url", sa.Text))
    op.add_column("access_logs", sa.Column("block_reason", sa.String(16)))
    op.add_column("access_logs", sa.Column("block_detail", sa.Text))
    op.add_column("access_logs", sa.Column("country_code", sa.String(2)))
    op.add_column("access_logs", sa.Column("ua_raw", sa.Text))
    op.add_column("access_logs", sa.Column("ua_browser", sa.String(128)))
    op.add_column("access_logs", sa.Column("ua_browser_version", sa.String(128)))
    op.add_column("access_logs", sa.Column("ua_os", sa.String(128)))
    op.add_column("access_logs", sa.Column("ua_os_version", sa.String(128)))
    op.add_column("access_logs", sa.Column("ua_device_type", sa.String(64)))
    op.add_column("access_logs", sa.Column("ua_device_brand", sa.String(128)))
    op.add_column("access_logs", sa.Column("ua_device_model", sa.String(128)))
    op.add_column("access_logs", sa.Column("ua_bot_name", sa.String(128)))
    bind.execute(
        sa.text(
            """
            UPDATE access_logs AS logs
            SET domain_name = domains.name,
                short_code = short_links.short_code,
                short_link_note = short_links.note,
                request_url = CASE WHEN domains.name IS NULL OR short_links.short_code IS NULL THEN NULL
                                   ELSE 'https://' || domains.name || '/' || short_links.short_code END,
                target_url = (SELECT url FROM target_urls WHERE target_urls.id = logs.target_url_id),
                country_code = LEFT(logs.country, 2),
                ua_raw = logs.ua_string,
                ua_device_type = logs.ua_platform,
                result = CASE WHEN logs.result IN ('denied', 'blocked') THEN 'blocked' ELSE logs.result END,
                block_reason = CASE WHEN logs.result IN ('denied', 'blocked') THEN 'other' ELSE NULL END,
                block_detail = CASE WHEN logs.result IN ('denied', 'blocked')
                                    THEN 'Historical record: the previous version did not store a specific reason.'
                                    ELSE NULL END
            FROM short_links
            LEFT JOIN domains ON domains.id = short_links.domain_id
            WHERE short_links.id = logs.short_link_id
            """
        )
    )
    op.alter_column("access_logs", "ip", nullable=True)
    op.alter_column("access_logs", "ip", type_=postgresql.INET, postgresql_using="pg_temp.safe_inet(ip)")
    op.create_check_constraint("ck_access_logs_result", "access_logs", "result IN ('allowed', 'blocked', 'error')")
    op.create_check_constraint(
        "ck_access_logs_block_reason",
        "access_logs",
        "block_reason IS NULL OR block_reason IN ('ip', 'proxy', 'country', 'bot', 'platform', 'referer', 'other')",
    )

    for name in ("access_logs_short_link_id_fkey", "fk_access_logs_domain", "access_logs_target_url_id_fkey"):
        op.drop_constraint(name, "access_logs", type_="foreignkey")
    for column in ("short_link_id", "domain_id", "target_url_id"):
        op.alter_column("access_logs", column, nullable=True)
    op.create_foreign_key("fk_access_logs_short_link", "access_logs", "short_links", ["short_link_id"], ["id"], ondelete="SET NULL")
    op.create_foreign_key("fk_access_logs_domain", "access_logs", "domains", ["domain_id"], ["id"], ondelete="SET NULL")
    op.create_foreign_key("fk_access_logs_target_url", "access_logs", "target_urls", ["target_url_id"], ["id"], ondelete="SET NULL")
    op.create_index("ix_access_logs_accessed_at_id", "access_logs", [sa.text("accessed_at DESC"), sa.text("id DESC")])
    op.create_index("ix_access_logs_domain_accessed_at_id", "access_logs", ["domain_id", sa.text("accessed_at DESC"), sa.text("id DESC")])
    op.create_index("ix_access_logs_short_link_accessed_at_id", "access_logs", ["short_link_id", sa.text("accessed_at DESC"), sa.text("id DESC")])
    op.create_index("ix_access_logs_result_accessed_at", "access_logs", ["result", sa.text("accessed_at DESC")])
    op.create_index("ix_access_logs_country_code_accessed_at", "access_logs", ["country_code", sa.text("accessed_at DESC")])
    op.create_index("ix_access_logs_block_reason_accessed_at", "access_logs", ["block_reason", sa.text("accessed_at DESC")])

    op.alter_column("ip_blacklist", "ip", type_=postgresql.INET, postgresql_using="pg_temp.safe_inet(ip)")
    op.drop_constraint("ip_blacklist_created_by_fkey", "ip_blacklist", type_="foreignkey")
    op.create_foreign_key("fk_ip_blacklist_created_by", "ip_blacklist", "users", ["created_by"], ["id"], ondelete="SET NULL")

    op.drop_table("short_link_permissions")
    op.drop_table("access_rules")
    op.drop_table("user_domains")
    op.drop_column("short_links", "description")
    op.drop_column("short_links", "default_action")
    op.drop_column("domains", "is_default")
    for column in ("country", "ua_string", "ua_platform", "accessed_at_plus8", "dedup_bucket"):
        op.drop_column("access_logs", column)

    # New-link defaults are application-owned after the historical backfill.
    for column in ("country_mode", "countries", "platform_mode", "platforms", "referer_mode", "referer_patterns", "block_proxy", "block_bot", "updated_at"):
        op.alter_column("link_policies", column, server_default=None)
    op.alter_column("user_domain_access", "created_at", server_default=None)


def downgrade() -> None:
    raise NotImplementedError("The data-preserving schema normalization is forward-only")
