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
    func,
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
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    domain_id = Column(
        UUID(as_uuid=True),
        ForeignKey("domains.id", name="fk_short_links_domain", ondelete="RESTRICT"),
        nullable=False,
    )
    short_code = Column(String(32), nullable=False)
    is_custom_alias = Column(
        Boolean, default=False, server_default=text("false"), nullable=False
    )
    name = Column(String(128), nullable=False)
    owner_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", name="fk_short_links_owner", ondelete="RESTRICT"),
        nullable=False,
    )
    is_active = Column(
        Boolean, default=True, server_default=text("true"), nullable=False
    )
    default_action = Column(
        String(16), default="deny", server_default=text("'deny'"), nullable=False
    )
    deleted_at = Column(DateTime(timezone=True), nullable=True)
    deleted_by = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", name="fk_short_links_deleted_by", ondelete="RESTRICT"),
        nullable=True,
    )
    created_at = Column(
        DateTime(timezone=True),
        default=now_utc,
        server_default=text("now()"),
        nullable=False,
    )
    updated_at = Column(
        DateTime(timezone=True),
        default=now_utc,
        onupdate=now_utc,
        server_default=text("now()"),
        nullable=False,
    )

    owner = relationship("User", foreign_keys=[owner_id])
    target_urls = relationship(
        "TargetUrl", cascade="all, delete-orphan", lazy="selectin"
    )
    access_rules = relationship(
        "AccessRule", cascade="all, delete-orphan", lazy="selectin"
    )
    permissions = relationship(
        "ShortLinkPermission", cascade="all, delete-orphan", lazy="selectin"
    )

    __table_args__ = (
        UniqueConstraint("domain_id", "short_code", name="uq_domain_short_code"),
        CheckConstraint(
            "default_action = ANY (ARRAY['allow', 'deny'])",
            name="ck_short_links_default_action",
        ),
        CheckConstraint(
            "deleted_by IS NULL OR deleted_at IS NOT NULL",
            name="ck_short_links_deleted_actor",
        ),
        CheckConstraint(
            "short_code = lower(btrim(short_code)) AND short_code ~ '^[a-z0-9_-]{3,32}$'",
            name="ck_short_links_short_code_canonical",
        ),
        Index("idx_short_links_domain_created_at", "domain_id", created_at.desc()),
        Index(
            "idx_short_links_domain_owner_created_at",
            "domain_id",
            "owner_id",
            created_at.desc(),
        ),
        Index(
            "idx_short_links_name_trgm",
            func.lower(name).label("name_lower"),
            postgresql_using="gin",
            postgresql_ops={"name_lower": "gin_trgm_ops"},
        ),
        Index(
            "idx_short_links_domain_code_pattern",
            "domain_id",
            "short_code",
            postgresql_ops={"short_code": "varchar_pattern_ops"},
        ),
    )


class ShortLinkPermission(Base):
    __tablename__ = "short_link_permissions"

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    short_link_id = Column(
        UUID(as_uuid=True),
        ForeignKey(
            "short_links.id", name="fk_short_link_permissions_link", ondelete="RESTRICT"
        ),
        nullable=False,
    )
    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey(
            "users.id", name="fk_short_link_permissions_user", ondelete="RESTRICT"
        ),
        nullable=False,
    )
    granted_by = Column(
        UUID(as_uuid=True),
        ForeignKey(
            "users.id", name="fk_short_link_permissions_granted_by", ondelete="RESTRICT"
        ),
        nullable=True,
    )
    created_at = Column(
        DateTime(timezone=True),
        default=now_utc,
        server_default=text("now()"),
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint("short_link_id", "user_id", name="uq_short_link_user"),
    )


class TargetUrl(Base):
    __tablename__ = "target_urls"

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    short_link_id = Column(
        UUID(as_uuid=True),
        ForeignKey(
            "short_links.id", name="target_urls_short_link_id_fkey", ondelete="CASCADE"
        ),
        nullable=False,
    )
    name = Column(String(128), nullable=True)
    url = Column(Text, nullable=False)
    url_type = Column(String(16), nullable=False)  # allowed / denied
    weight = Column(Integer, default=1, server_default=text("1"), nullable=False)
    is_active = Column(
        Boolean, default=True, server_default=text("true"), nullable=False
    )
    created_at = Column(
        DateTime(timezone=True),
        default=now_utc,
        server_default=text("now()"),
        nullable=False,
    )
    updated_at = Column(
        DateTime(timezone=True),
        default=now_utc,
        onupdate=now_utc,
        server_default=text("now()"),
        nullable=False,
    )

    __table_args__ = (
        CheckConstraint(
            "url_type = ANY (ARRAY['allowed', 'denied'])",
            name="ck_target_urls_type",
        ),
        CheckConstraint("weight >= 1", name="ck_target_urls_weight"),
    )
