import uuid

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import INET, UUID

from app.db.base import Base, now_utc


class IpBlacklist(Base):
    __tablename__ = "ip_blacklist"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    ip = Column(INET, unique=True, nullable=False)
    reason = Column(Text)
    created_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    created_at = Column(DateTime(timezone=True), default=now_utc, nullable=False)


class IpReputation(Base):
    __tablename__ = "ip_reputation"

    ip = Column(INET, primary_key=True)
    is_proxy = Column(Boolean, nullable=False)
    proxy_type = Column(String(64))
    checked_at = Column(DateTime(timezone=True), default=now_utc, nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
