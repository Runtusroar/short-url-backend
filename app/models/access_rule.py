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
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID

from app.core.database import Base
from app.models.base import now_utc
from app.models.enums import ClientRequirement, ProxyRequirement


class AccessRule(Base):
    __tablename__ = "access_rules"

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    short_link_id = Column(
        UUID(as_uuid=True),
        ForeignKey(
            "short_links.id", name="fk_access_rules_short_link", ondelete="CASCADE"
        ),
        nullable=False,
    )
    name = Column(String(128), nullable=False)
    action = Column(String(16), nullable=False)  # allow / deny
    priority = Column(Integer, default=0, server_default=text("0"), nullable=False)
    countries = Column(
        JSONB, default=list, server_default=text("'[]'::jsonb"), nullable=False
    )
    ua_platforms = Column(
        JSONB, default=list, server_default=text("'[]'::jsonb"), nullable=False
    )
    referer_patterns = Column(
        JSONB, default=list, server_default=text("'[]'::jsonb"), nullable=False
    )
    client_requirement = Column(
        String(16),
        default=ClientRequirement.ANY,
        server_default=text("'any'"),
        nullable=False,
    )
    proxy_requirement = Column(
        String(16),
        default=ProxyRequirement.ANY,
        server_default=text("'any'"),
        nullable=False,
    )
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
        UniqueConstraint(
            "short_link_id", "priority", name="uq_access_rules_link_priority"
        ),
        CheckConstraint(
            "action = ANY (ARRAY['allow', 'deny'])", name="ck_access_rules_action"
        ),
        CheckConstraint(
            "client_requirement = ANY (ARRAY['any', 'human', 'bot'])",
            name="ck_access_rules_client_requirement",
        ),
        CheckConstraint(
            "proxy_requirement = ANY (ARRAY['any', 'non_proxy', 'proxy'])",
            name="ck_access_rules_proxy_requirement",
        ),
        CheckConstraint(
            "jsonb_typeof(countries) = 'array'", name="ck_access_rules_countries_array"
        ),
        CheckConstraint(
            "jsonb_typeof(ua_platforms) = 'array'",
            name="ck_access_rules_ua_platforms_array",
        ),
        CheckConstraint(
            "jsonb_typeof(referer_patterns) = 'array'",
            name="ck_access_rules_referer_patterns_array",
        ),
        Index(
            "idx_access_rules_link_active_priority",
            "short_link_id",
            "priority",
            "id",
            postgresql_where=text("is_active"),
        ),
    )
