import uuid

from sqlalchemy import CheckConstraint, Column, DateTime, ForeignKey, Index, String, Text, text
from sqlalchemy.dialects.postgresql import INET, UUID

from app.db.base import Base, now_utc


class AccessLog(Base):
    __tablename__ = "access_logs"
    __table_args__ = (
        CheckConstraint("result IN ('allowed', 'blocked', 'error')", name="ck_access_logs_result"),
        CheckConstraint(
            "block_reason IS NULL OR block_reason IN ('ip', 'proxy', 'country', 'bot', 'platform', 'referer', 'other')",
            name="ck_access_logs_block_reason",
        ),
        Index("ix_access_logs_accessed_at_id", text("accessed_at DESC"), text("id DESC")),
        Index("ix_access_logs_domain_accessed_at_id", "domain_id", text("accessed_at DESC"), text("id DESC")),
        Index("ix_access_logs_short_link_accessed_at_id", "short_link_id", text("accessed_at DESC"), text("id DESC")),
        Index("ix_access_logs_result_accessed_at", "result", text("accessed_at DESC")),
        Index("ix_access_logs_country_code_accessed_at", "country_code", text("accessed_at DESC")),
        Index("ix_access_logs_block_reason_accessed_at", "block_reason", text("accessed_at DESC")),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    short_link_id = Column(UUID(as_uuid=True), ForeignKey("short_links.id", ondelete="SET NULL"), nullable=True)
    domain_id = Column(UUID(as_uuid=True), ForeignKey("domains.id", ondelete="SET NULL"), nullable=True)
    target_url_id = Column(UUID(as_uuid=True), ForeignKey("target_urls.id", ondelete="SET NULL"), nullable=True)
    request_url = Column(Text)
    domain_name = Column(String(255))
    short_code = Column(String(32))
    short_link_note = Column(Text)
    target_url = Column(Text)
    result = Column(String(16), nullable=False)
    block_reason = Column(String(16))
    block_detail = Column(Text)
    ip = Column(INET)
    country_code = Column(String(2))
    referer = Column(Text)
    ua_raw = Column(Text)
    ua_browser = Column(String(128))
    ua_browser_version = Column(String(128))
    ua_os = Column(String(128))
    ua_os_version = Column(String(128))
    ua_device_type = Column(String(64))
    ua_device_brand = Column(String(128))
    ua_device_model = Column(String(128))
    ua_bot_name = Column(String(128))
    accessed_at = Column(DateTime(timezone=True), default=now_utc, nullable=False)
