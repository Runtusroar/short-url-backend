import uuid

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import INET, UUID

from app.core.database import Base
from app.models.base import now_utc


class IpBlacklist(Base):
    __tablename__ = "ip_blacklist"

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    ip = Column(INET, nullable=False)
    reason = Column(Text, nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=True)
    created_by = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", name="fk_ip_blacklist_created_by", ondelete="RESTRICT"),
        nullable=True,
    )
    created_at = Column(
        DateTime(timezone=True),
        default=now_utc,
        server_default=text("now()"),
        nullable=False,
    )
    removed_at = Column(DateTime(timezone=True), nullable=True)
    removed_by = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", name="fk_ip_blacklist_removed_by", ondelete="RESTRICT"),
        nullable=True,
    )
    removal_reason = Column(Text, nullable=True)

    __table_args__ = (
        UniqueConstraint("ip", name="ip_blacklist_ip_key"),
        CheckConstraint("btrim(reason) <> ''", name="ck_ip_blacklist_reason"),
        CheckConstraint(
            "removed_by IS NULL OR removed_at IS NOT NULL",
            name="ck_ip_blacklist_removed_actor",
        ),
        Index(
            "idx_ip_blacklist_active_expires_at",
            "expires_at",
            postgresql_where=text("removed_at IS NULL"),
        ),
    )
