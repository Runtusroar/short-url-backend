import uuid

from sqlalchemy import Boolean, CheckConstraint, Column, DateTime, String, text
from sqlalchemy.dialects.postgresql import UUID

from app.core.database import Base
from app.models.base import now_utc


class User(Base):
    __tablename__ = "users"

    id = Column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text("gen_random_uuid()")
    )
    username = Column(String(64), unique=True, nullable=False)
    password_hash = Column(String(255), nullable=False)
    role = Column(String(16), nullable=False)
    is_active = Column(Boolean, default=True, server_default=text("true"), nullable=False)
    created_at = Column(DateTime(timezone=True), default=now_utc, server_default=text("now()"), nullable=False)
    updated_at = Column(
        DateTime(timezone=True), default=now_utc, onupdate=now_utc, server_default=text("now()"), nullable=False
    )

    __table_args__ = (
        CheckConstraint("role IN ('admin', 'operator', 'client')", name="ck_users_role"),
        CheckConstraint("username = lower(btrim(username))", name="ck_users_username_lower"),
    )
