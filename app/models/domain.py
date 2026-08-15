import uuid

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID

from app.core.database import Base
from app.models.base import now_utc


class Domain(Base):
    __tablename__ = "domains"

    id = Column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text("gen_random_uuid()")
    )
    name = Column(String(255), unique=True, nullable=False)
    timezone = Column(String(64), default="Asia/Shanghai", server_default=text("'Asia/Shanghai'"), nullable=False)
    is_active = Column(Boolean, default=True, server_default=text("true"), nullable=False)
    is_default = Column(Boolean, default=False, server_default=text("false"), nullable=False)
    created_at = Column(DateTime(timezone=True), default=now_utc, server_default=text("now()"), nullable=False)
    updated_at = Column(
        DateTime(timezone=True), default=now_utc, onupdate=now_utc, server_default=text("now()"), nullable=False
    )

    __table_args__ = (
        CheckConstraint(
            r"name = lower(btrim(name)) AND length(name) <= 253 AND name ~ '^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)*$'",
            name="ck_domains_name_lower_host",
        ),
        Index("uq_domains_one_default", "is_default", unique=True, postgresql_where=text("is_default")),
    )


class UserDomain(Base):
    __tablename__ = "user_domains"

    id = Column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text("gen_random_uuid()")
    )
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    domain_id = Column(
        UUID(as_uuid=True), ForeignKey("domains.id", ondelete="RESTRICT"), nullable=False
    )
    granted_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=True)
    created_at = Column(DateTime(timezone=True), default=now_utc, server_default=text("now()"), nullable=False)

    __table_args__ = (
        UniqueConstraint("user_id", "domain_id", name="uq_user_domain"),
        Index("idx_user_domains_domain", "domain_id"),
    )
