import uuid

from sqlalchemy import Column, DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID

from app.core.database import Base
from app.models.base import now_utc


class IpBlacklist(Base):
    __tablename__ = "ip_blacklist"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    ip = Column(String(64), unique=True, nullable=False)
    reason = Column(Text)
    created_by = Column(UUID(as_uuid=True), ForeignKey("users.id"))
    created_at = Column(DateTime(timezone=True), default=now_utc, nullable=False)
