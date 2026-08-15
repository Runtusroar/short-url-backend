import uuid

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.core.database import Base
from app.models.base import now_utc


class ShortLink(Base):
    __tablename__ = "short_links"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    domain_id = Column(UUID(as_uuid=True), ForeignKey("domains.id"), nullable=False)
    short_code = Column(String(32), nullable=False)
    is_custom_alias = Column(Boolean, default=False, nullable=False)
    description = Column(Text)
    owner_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    default_action = Column(String(16), default="allow", nullable=False)
    created_at = Column(DateTime(timezone=True), default=now_utc, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=now_utc, onupdate=now_utc, nullable=False)

    owner = relationship("User")
    target_urls = relationship("TargetUrl", cascade="all, delete-orphan", lazy="selectin")
    access_rules = relationship("AccessRule", cascade="all, delete-orphan", lazy="selectin")
    permissions = relationship("ShortLinkPermission", cascade="all, delete-orphan", lazy="selectin")

    __table_args__ = (UniqueConstraint("domain_id", "short_code", name="uq_domain_short_code"),)


class ShortLinkPermission(Base):
    __tablename__ = "short_link_permissions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    short_link_id = Column(
        UUID(as_uuid=True), ForeignKey("short_links.id", ondelete="CASCADE"), nullable=False
    )
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    created_at = Column(DateTime(timezone=True), default=now_utc, nullable=False)

    __table_args__ = (UniqueConstraint("short_link_id", "user_id", name="uq_short_link_user"),)


class TargetUrl(Base):
    __tablename__ = "target_urls"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    short_link_id = Column(
        UUID(as_uuid=True), ForeignKey("short_links.id", ondelete="CASCADE"), nullable=False
    )
    url = Column(Text, nullable=False)
    url_type = Column(String(16), nullable=False)  # allowed / denied
    weight = Column(Integer, default=1, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime(timezone=True), default=now_utc, nullable=False)
