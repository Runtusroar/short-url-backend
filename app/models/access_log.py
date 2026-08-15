import uuid

from sqlalchemy import BigInteger, Column, Date, DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID

from app.core.database import Base
from app.models.base import now_utc


class AccessLog(Base):
    __tablename__ = "access_logs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    short_link_id = Column(
        UUID(as_uuid=True), ForeignKey("short_links.id", ondelete="CASCADE"), nullable=False
    )
    domain_id = Column(UUID(as_uuid=True), ForeignKey("domains.id"), nullable=False)
    target_url_id = Column(UUID(as_uuid=True), ForeignKey("target_urls.id", ondelete="SET NULL"), nullable=True)
    result = Column(String(16), nullable=False)  # allowed / denied / blocked
    ip = Column(String(64), nullable=False)
    country = Column(String(8))
    ua_string = Column(Text)
    ua_platform = Column(String(64))
    referer = Column(Text)
    accessed_at = Column(DateTime(timezone=True), default=now_utc, nullable=False)
    accessed_at_plus8 = Column(Date(), nullable=False)
    dedup_bucket = Column(BigInteger, nullable=False)
