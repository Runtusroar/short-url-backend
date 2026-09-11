import uuid

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship

from app.db.base import Base, now_utc


class ShortLink(Base):
    __tablename__ = "short_links"
    __table_args__ = (UniqueConstraint("domain_id", "short_code", name="uq_domain_short_code"),)

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    domain_id = Column(UUID(as_uuid=True), ForeignKey("domains.id"), nullable=False)
    short_code = Column(String(32), nullable=False)
    is_custom_alias = Column(Boolean, default=False, nullable=False)
    note = Column(Text)
    owner_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime(timezone=True), default=now_utc, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=now_utc, onupdate=now_utc, nullable=False)

    owner = relationship("User")
    target_urls = relationship("TargetUrl", cascade="all, delete-orphan", lazy="selectin")
    policy = relationship("LinkPolicy", cascade="all, delete-orphan", uselist=False, lazy="selectin")


class TargetUrl(Base):
    __tablename__ = "target_urls"
    __table_args__ = (
        CheckConstraint("url_type IN ('allowed', 'blocked')", name="ck_target_urls_type"),
        CheckConstraint("weight >= 0", name="ck_target_urls_weight_nonnegative"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    short_link_id = Column(UUID(as_uuid=True), ForeignKey("short_links.id", ondelete="CASCADE"), nullable=False)
    url = Column(Text, nullable=False)
    url_type = Column(String(16), nullable=False)
    weight = Column(Integer, default=1, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime(timezone=True), default=now_utc, nullable=False)


class LinkPolicy(Base):
    __tablename__ = "link_policies"
    __table_args__ = (
        CheckConstraint("country_mode IN ('off', 'allow', 'block')", name="ck_link_policies_country_mode"),
        CheckConstraint("platform_mode IN ('off', 'allow', 'block')", name="ck_link_policies_platform_mode"),
        CheckConstraint("referer_mode IN ('off', 'allow', 'block')", name="ck_link_policies_referer_mode"),
    )

    short_link_id = Column(UUID(as_uuid=True), ForeignKey("short_links.id", ondelete="CASCADE"), primary_key=True)
    country_mode = Column(String(16), nullable=False, default="off")
    countries = Column(JSONB, nullable=False, default=list)
    platform_mode = Column(String(16), nullable=False, default="off")
    platforms = Column(JSONB, nullable=False, default=list)
    referer_mode = Column(String(16), nullable=False, default="off")
    referer_patterns = Column(JSONB, nullable=False, default=list)
    block_proxy = Column(Boolean, nullable=False, default=False)
    block_bot = Column(Boolean, nullable=False, default=False)
    updated_at = Column(DateTime(timezone=True), default=now_utc, onupdate=now_utc, nullable=False)
