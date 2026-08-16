import uuid

from sqlalchemy import (
    CHAR,
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import INET, JSONB, UUID

from app.core.database import Base
from app.models.base import now_utc


class AccessLog(Base):
    __tablename__ = "access_logs"

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    short_link_id = Column(UUID(as_uuid=True), ForeignKey("short_links.id", name="fk_access_logs_short_link", ondelete="RESTRICT"), nullable=False)
    domain_id = Column(UUID(as_uuid=True), ForeignKey("domains.id", name="fk_access_logs_domain", ondelete="RESTRICT"), nullable=False)
    target_url_id = Column(UUID(as_uuid=True), ForeignKey("target_urls.id", name="fk_access_logs_target_url", ondelete="SET NULL"), nullable=True)
    matched_rule_id = Column(UUID(as_uuid=True), ForeignKey("access_rules.id", name="fk_access_logs_matched_rule", ondelete="SET NULL"), nullable=True)
    result = Column(String(16), nullable=False)  # allowed / denied / blocked
    # Client identity can be genuinely unavailable when neither socket nor trusted proxy supplied an address.
    client_ip = Column(INET, nullable=True)
    country = Column(CHAR(2))
    user_agent = Column(Text)
    ua_platform = Column(String(64))
    referer = Column(Text)
    accessed_at = Column(DateTime(timezone=True), default=now_utc, server_default=text("now()"), nullable=False)
    access_date = Column(Date(), nullable=False)
    dedup_bucket = Column(BigInteger, nullable=False)
    decision_reason = Column(String(16), nullable=False)
    matched_rule_name = Column(String(128), nullable=True)
    target_url_snapshot = Column(Text, nullable=True)
    request_host = Column(String(255), nullable=True)
    request_method = Column(String(8), nullable=False)
    proxy_check_status = Column(String(16), nullable=False)
    is_anonymous = Column(Boolean, nullable=True)
    proxy_types = Column(JSONB, default=list, server_default=text("'[]'::jsonb"), nullable=False)
    proxy_source = Column(String(32), nullable=True)

    __table_args__ = (
        CheckConstraint("result IN ('allowed', 'denied', 'blocked')", name="ck_access_logs_result"),
        CheckConstraint("country IS NULL OR country ~ '^[A-Z]{2}$'", name="ck_access_logs_country"),
        CheckConstraint("decision_reason IN ('legacy_unknown', 'blacklist', 'matched_rule', 'default_action', 'no_target')", name="ck_access_logs_decision_reason"),
        CheckConstraint("request_method IN ('GET', 'HEAD')", name="ck_access_logs_request_method"),
        CheckConstraint("proxy_check_status IN ('skipped', 'cached', 'checked', 'assumed_bot', 'error')", name="ck_access_logs_proxy_check_status"),
        CheckConstraint("proxy_source IS NULL OR proxy_source IN ('maxmind_insights', 'assumed_bot')", name="ck_access_logs_proxy_source"),
        CheckConstraint("jsonb_typeof(proxy_types) = 'array'", name="ck_access_logs_proxy_types_array"),
        Index("idx_access_logs_domain_accessed_at", "domain_id", accessed_at.desc()),
        Index("idx_access_logs_link_accessed_at", "short_link_id", accessed_at.desc()),
        Index("idx_access_logs_link_access_date", "short_link_id", access_date.desc()),
        Index("idx_access_logs_link_client_ip_dedup", "short_link_id", "client_ip", "dedup_bucket"),
        Index("idx_access_logs_domain_result_accessed_at", "domain_id", "result", accessed_at.desc()),
        Index("idx_access_logs_domain_country_accessed_at", "domain_id", "country", accessed_at.desc()),
    )
