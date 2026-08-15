import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Column,
    String,
    Boolean,
    Text,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    BigInteger,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship

from app.database import Base


def now_utc():
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    username = Column(String(64), unique=True, nullable=False)
    password_hash = Column(String(255), nullable=False)
    role = Column(String(16), nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime(timezone=True), default=now_utc, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=now_utc, onupdate=now_utc, nullable=False)


class Domain(Base):
    __tablename__ = "domains"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(255), unique=True, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    is_default = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime(timezone=True), default=now_utc, nullable=False)


class UserDomain(Base):
    __tablename__ = "user_domains"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    domain_id = Column(
        UUID(as_uuid=True), ForeignKey("domains.id", ondelete="CASCADE"), nullable=False
    )
    created_at = Column(DateTime(timezone=True), default=now_utc, nullable=False)

    __table_args__ = (UniqueConstraint("user_id", "domain_id", name="uq_user_domain"),)


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


class IpBlacklist(Base):
    __tablename__ = "ip_blacklist"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    ip = Column(String(64), unique=True, nullable=False)
    reason = Column(Text)
    created_by = Column(UUID(as_uuid=True), ForeignKey("users.id"))
    created_at = Column(DateTime(timezone=True), default=now_utc, nullable=False)


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
