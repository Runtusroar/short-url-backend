import uuid

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.core.database import Base
from app.models.base import now_utc


class ShortLink(Base):
    __tablename__ = "short_links"

    id = Column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text("gen_random_uuid()")
    )
    domain_id = Column(
        UUID(as_uuid=True), ForeignKey("domains.id", ondelete="RESTRICT"), nullable=False
    )
    short_code = Column(String(32), nullable=False)
    is_custom_alias = Column(Boolean, default=False, server_default=text("false"), nullable=False)
    name = Column(String(128), nullable=False)
    owner_id = Column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    is_active = Column(Boolean, default=True, server_default=text("true"), nullable=False)
    default_action = Column(String(16), default="deny", server_default=text("'deny'"), nullable=False)
    deleted_at = Column(DateTime(timezone=True), nullable=True)
    deleted_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=True)
    created_at = Column(DateTime(timezone=True), default=now_utc, server_default=text("now()"), nullable=False)
    updated_at = Column(
        DateTime(timezone=True), default=now_utc, onupdate=now_utc, server_default=text("now()"), nullable=False
    )

    owner = relationship("User", foreign_keys=[owner_id])
    target_urls = relationship("TargetUrl", cascade="all, delete-orphan", lazy="selectin")
    access_rules = relationship("AccessRule", cascade="all, delete-orphan", lazy="selectin")
    permissions = relationship("ShortLinkPermission", cascade="all, delete-orphan", lazy="selectin")

    __table_args__ = (
        UniqueConstraint("domain_id", "short_code", name="uq_domain_short_code"),
        CheckConstraint("default_action IN ('allow', 'deny')", name="ck_short_links_default_action"),
        CheckConstraint("deleted_by IS NULL OR deleted_at IS NOT NULL", name="ck_short_links_deleted_actor"),
        Index("idx_short_links_domain_created_at", "domain_id", created_at.desc()),
        Index("idx_short_links_domain_owner_created_at", "domain_id", "owner_id", created_at.desc()),
    )


class ShortLinkPermission(Base):
    __tablename__ = "short_link_permissions"

    id = Column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text("gen_random_uuid()")
    )
    short_link_id = Column(
        UUID(as_uuid=True), ForeignKey("short_links.id", ondelete="RESTRICT"), nullable=False
    )
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    granted_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=True)
    created_at = Column(DateTime(timezone=True), default=now_utc, server_default=text("now()"), nullable=False)

    __table_args__ = (UniqueConstraint("short_link_id", "user_id", name="uq_short_link_user"),)


class TargetUrl(Base):
    __tablename__ = "target_urls"

    id = Column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text("gen_random_uuid()")
    )
    short_link_id = Column(
        UUID(as_uuid=True), ForeignKey("short_links.id", ondelete="CASCADE"), nullable=False
    )
    name = Column(String(128), nullable=True)
    url = Column(Text, nullable=False)
    url_type = Column(String(16), nullable=False)  # allowed / denied
    weight = Column(Integer, default=1, server_default=text("1"), nullable=False)
    is_active = Column(Boolean, default=True, server_default=text("true"), nullable=False)
    created_at = Column(DateTime(timezone=True), default=now_utc, server_default=text("now()"), nullable=False)
    updated_at = Column(
        DateTime(timezone=True), default=now_utc, onupdate=now_utc, server_default=text("now()"), nullable=False
    )

    __table_args__ = (
        CheckConstraint("url_type IN ('allowed', 'denied')", name="ck_target_urls_type"),
        CheckConstraint("weight >= 1", name="ck_target_urls_weight"),
    )
