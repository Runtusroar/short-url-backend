import uuid

from sqlalchemy import Boolean, Column, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import JSONB, UUID

from app.core.database import Base


class AccessRule(Base):
    __tablename__ = "access_rules"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    short_link_id = Column(
        UUID(as_uuid=True), ForeignKey("short_links.id", ondelete="CASCADE"), nullable=False
    )
    action = Column(String(16), nullable=False)  # allow / deny
    priority = Column(Integer, default=0, nullable=False)
    countries = Column(JSONB, default=list)
    ua_platforms = Column(JSONB, default=list)
    referer_pattern = Column(String(255))
    allow_proxy = Column(Boolean, default=True, nullable=False)
    allow_bot = Column(Boolean, default=True, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
