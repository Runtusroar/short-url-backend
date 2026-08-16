"""preserve explainable access-log history

Revision ID: a73f0b9d4216
Revises: e52d9a6c8031
"""

import ipaddress
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import sqlalchemy as sa
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "a73f0b9d4216"
down_revision = "e52d9a6c8031"
branch_labels = None
depends_on = None

_COUNTRY = re.compile(r"^[A-Z]{2}$")


def _count(statement: str) -> int:
    return int(op.get_bind().execute(sa.text(statement)).scalar_one())


def _validate_legacy_facts() -> None:
    """Fail closed before schema writes, without including stored values in errors."""
    invalid_ips = 0
    for value in op.get_bind().execute(sa.text("SELECT ip FROM access_logs")).scalars():
        try:
            ipaddress.ip_address(value)
        except (TypeError, ValueError):
            invalid_ips += 1
    if invalid_ips:
        raise RuntimeError(f"access log IP invariant violated: {invalid_ips}")
    if _count("SELECT count(*) FROM access_logs WHERE result NOT IN ('allowed', 'denied', 'blocked')"):
        raise RuntimeError("access log result invariant violated")
    invalid_countries = 0
    for value in op.get_bind().execute(sa.text("SELECT country FROM access_logs WHERE country IS NOT NULL")).scalars():
        if not isinstance(value, str) or _COUNTRY.fullmatch(value) is None:
            invalid_countries += 1
    if invalid_countries:
        raise RuntimeError(f"access log country invariant violated: {invalid_countries}")
    invalid_timezones = 0
    for value in op.get_bind().execute(sa.text("SELECT timezone FROM domains")).scalars():
        try:
            ZoneInfo(value)
        except (TypeError, ValueError, ZoneInfoNotFoundError):
            invalid_timezones += 1
    if invalid_timezones:
        raise RuntimeError(f"access log domain timezone invariant violated: {invalid_timezones}")


def _replace_foreign_key(column: str, name: str, referred_table: str, ondelete: str | None) -> None:
    for foreign_key in inspect(op.get_bind()).get_foreign_keys("access_logs"):
        if foreign_key["constrained_columns"] != [column]:
            continue
        old_name = foreign_key["name"]
        if not isinstance(old_name, str) or not old_name:
            raise RuntimeError("access log foreign key has no inspectable name")
        op.drop_constraint(old_name, "access_logs", type_="foreignkey")
        break
    op.create_foreign_key(name, "access_logs", referred_table, [column], ["id"], ondelete=ondelete)


def _reject_unsafe_downgrade() -> None:
    """Reject facts the Phase 3 columns cannot faithfully carry before any DDL/DML."""
    if _count("SELECT count(*) FROM access_logs WHERE client_ip IS NULL"):
        raise RuntimeError("access log downgrade client IP invariant violated")
    if _count("SELECT count(*) FROM access_logs WHERE decision_reason NOT IN ('legacy_unknown', 'blacklist')"):
        raise RuntimeError("access log downgrade decision invariant violated")
    if _count(
        """
        SELECT count(*) FROM access_logs
        WHERE proxy_check_status NOT IN ('skipped', 'assumed_bot')
           OR proxy_types IS NULL
           OR jsonb_typeof(proxy_types) <> 'array'
           OR proxy_types <> '[]'::jsonb
           OR (proxy_check_status = 'assumed_bot' AND (is_anonymous IS NOT TRUE OR proxy_source <> 'assumed_bot'))
           OR (proxy_check_status = 'skipped' AND (is_anonymous IS NOT NULL OR proxy_source IS NOT NULL))
        """
    ):
        raise RuntimeError("access log downgrade proxy invariant violated")


def upgrade() -> None:
    _validate_legacy_facts()

    for column in (
        sa.Column("client_ip", postgresql.INET(), nullable=True),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.Column("access_date", sa.Date(), nullable=True),
        sa.Column("matched_rule_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("matched_rule_name", sa.String(length=128), nullable=True),
        sa.Column("target_url_snapshot", sa.Text(), nullable=True),
        sa.Column("decision_reason", sa.String(length=16), nullable=True),
        sa.Column("request_host", sa.String(length=255), nullable=True),
        sa.Column("request_method", sa.String(length=8), nullable=True),
        sa.Column("proxy_check_status", sa.String(length=16), nullable=True),
        sa.Column("is_anonymous", sa.Boolean(), nullable=True),
        sa.Column("proxy_types", postgresql.JSONB(), nullable=True),
        sa.Column("proxy_source", sa.String(length=32), nullable=True),
    ):
        op.add_column("access_logs", column)

    op.execute(
        """
        UPDATE access_logs AS log
        SET client_ip = log.ip::inet,
            user_agent = log.ua_string,
            access_date = (log.accessed_at AT TIME ZONE domain.timezone)::date,
            target_url_snapshot = (SELECT target.url FROM target_urls AS target WHERE target.id = log.target_url_id),
            request_host = domain.name,
            request_method = 'GET',
            decision_reason = CASE WHEN log.result = 'blocked' THEN 'blacklist' ELSE 'legacy_unknown' END,
            proxy_check_status = CASE WHEN log.ua_platform = 'bot' THEN 'assumed_bot' ELSE 'skipped' END,
            is_anonymous = CASE WHEN log.ua_platform = 'bot' THEN true ELSE NULL END,
            proxy_types = '[]'::jsonb,
            proxy_source = CASE WHEN log.ua_platform = 'bot' THEN 'assumed_bot' ELSE NULL END
        FROM domains AS domain
        WHERE domain.id = log.domain_id
        """
    )
    op.alter_column("access_logs", "country", existing_type=sa.String(length=8), type_=sa.CHAR(length=2), postgresql_using="country::char(2)")
    for column_name, column_type in (
        ("access_date", sa.Date()),
        ("decision_reason", sa.String(length=16)),
        ("request_method", sa.String(length=8)),
        ("proxy_check_status", sa.String(length=16)),
        ("proxy_types", postgresql.JSONB()),
    ):
        op.alter_column("access_logs", column_name, existing_type=column_type, nullable=False)
    op.alter_column("access_logs", "accessed_at", existing_type=sa.DateTime(timezone=True), existing_nullable=False, server_default=sa.text("now()"))
    op.alter_column("access_logs", "proxy_types", existing_type=postgresql.JSONB(), existing_nullable=False, server_default=sa.text("'[]'::jsonb"))

    _replace_foreign_key("short_link_id", "fk_access_logs_short_link", "short_links", "RESTRICT")
    _replace_foreign_key("domain_id", "fk_access_logs_domain", "domains", "RESTRICT")
    _replace_foreign_key("target_url_id", "fk_access_logs_target_url", "target_urls", "SET NULL")
    op.create_foreign_key("fk_access_logs_matched_rule", "access_logs", "access_rules", ["matched_rule_id"], ["id"], ondelete="SET NULL")

    op.create_check_constraint("ck_access_logs_result", "access_logs", "result IN ('allowed', 'denied', 'blocked')")
    op.create_check_constraint("ck_access_logs_country", "access_logs", "country IS NULL OR country ~ '^[A-Z]{2}$'")
    op.create_check_constraint("ck_access_logs_decision_reason", "access_logs", "decision_reason IN ('legacy_unknown', 'blacklist', 'matched_rule', 'default_action', 'no_target')")
    op.create_check_constraint("ck_access_logs_request_method", "access_logs", "request_method IN ('GET', 'HEAD')")
    op.create_check_constraint("ck_access_logs_proxy_check_status", "access_logs", "proxy_check_status IN ('skipped', 'cached', 'checked', 'assumed_bot', 'error')")
    op.create_check_constraint("ck_access_logs_proxy_source", "access_logs", "proxy_source IS NULL OR proxy_source IN ('maxmind_insights', 'assumed_bot')")
    op.create_check_constraint("ck_access_logs_proxy_types_array", "access_logs", "jsonb_typeof(proxy_types) = 'array'")

    op.create_index("idx_access_logs_domain_accessed_at", "access_logs", ["domain_id", sa.text("accessed_at DESC")])
    op.create_index("idx_access_logs_link_accessed_at", "access_logs", ["short_link_id", sa.text("accessed_at DESC")])
    op.create_index("idx_access_logs_link_access_date", "access_logs", ["short_link_id", sa.text("access_date DESC")])
    op.create_index("idx_access_logs_link_client_ip_dedup", "access_logs", ["short_link_id", "client_ip", "dedup_bucket"])
    op.create_index("idx_access_logs_domain_result_accessed_at", "access_logs", ["domain_id", "result", sa.text("accessed_at DESC")])
    op.create_index("idx_access_logs_domain_country_accessed_at", "access_logs", ["domain_id", "country", sa.text("accessed_at DESC")])

    for name in ("idx_access_logs_domain", "idx_access_logs_short_link", "idx_access_logs_plus8", "idx_access_logs_dedup"):
        op.drop_index(name, table_name="access_logs", if_exists=True)
    op.drop_index("idx_short_links_domain", table_name="short_links", if_exists=True)
    op.drop_column("access_logs", "accessed_at_plus8")
    op.drop_column("access_logs", "ua_string")
    op.drop_column("access_logs", "ip")


def downgrade() -> None:
    _reject_unsafe_downgrade()

    op.add_column("access_logs", sa.Column("ip", sa.String(length=64), nullable=True))
    op.add_column("access_logs", sa.Column("ua_string", sa.Text(), nullable=True))
    op.add_column("access_logs", sa.Column("accessed_at_plus8", sa.Date(), nullable=True))
    op.execute("UPDATE access_logs SET ip = host(client_ip), ua_string = user_agent, accessed_at_plus8 = (accessed_at AT TIME ZONE 'Asia/Shanghai')::date")
    op.alter_column("access_logs", "ip", existing_type=sa.String(length=64), nullable=False)
    op.alter_column("access_logs", "accessed_at_plus8", existing_type=sa.Date(), nullable=False)

    for name in (
        "idx_access_logs_domain_accessed_at", "idx_access_logs_link_accessed_at", "idx_access_logs_link_access_date",
        "idx_access_logs_link_client_ip_dedup", "idx_access_logs_domain_result_accessed_at", "idx_access_logs_domain_country_accessed_at",
    ):
        op.drop_index(name, table_name="access_logs")
    for name in (
        "ck_access_logs_proxy_types_array", "ck_access_logs_proxy_source", "ck_access_logs_proxy_check_status",
        "ck_access_logs_request_method", "ck_access_logs_decision_reason", "ck_access_logs_country", "ck_access_logs_result",
    ):
        op.drop_constraint(name, "access_logs", type_="check")
    op.drop_constraint("fk_access_logs_matched_rule", "access_logs", type_="foreignkey")
    # Logs remain append-only across every supported downgrade path.
    _replace_foreign_key("short_link_id", "fk_access_logs_short_link", "short_links", "RESTRICT")
    _replace_foreign_key("domain_id", "fk_access_logs_domain", "domains", None)
    _replace_foreign_key("target_url_id", "fk_access_logs_target_url", "target_urls", "SET NULL")
    op.alter_column("access_logs", "country", existing_type=sa.CHAR(length=2), type_=sa.String(length=8), postgresql_using="country::varchar(8)")
    op.alter_column("access_logs", "accessed_at", existing_type=sa.DateTime(timezone=True), existing_nullable=False, server_default=None)
    op.alter_column("access_logs", "proxy_types", existing_type=postgresql.JSONB(), existing_nullable=False, server_default=None)
    for name in (
        "proxy_source", "proxy_types", "is_anonymous", "proxy_check_status", "request_method", "request_host",
        "decision_reason", "target_url_snapshot", "matched_rule_name", "matched_rule_id", "access_date", "user_agent", "client_ip",
    ):
        op.drop_column("access_logs", name)
    op.create_index("idx_short_links_domain", "short_links", ["domain_id"], unique=False)
    op.create_index("idx_access_logs_domain", "access_logs", ["domain_id"], unique=False)
    op.create_index("idx_access_logs_short_link", "access_logs", ["short_link_id"], unique=False)
    op.create_index("idx_access_logs_plus8", "access_logs", ["short_link_id", "accessed_at_plus8"], unique=False)
    op.create_index("idx_access_logs_dedup", "access_logs", ["short_link_id", "ip", "dedup_bucket"], unique=False)
