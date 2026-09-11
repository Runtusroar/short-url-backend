"""Best-effort persistence for immutable redirect access snapshots."""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy.exc import SQLAlchemyError

from app.db.models import AccessLog

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class AccessLogSnapshot:
    short_link_id: UUID
    domain_id: UUID
    target_url_id: UUID | None
    request_url: str
    domain_name: str
    short_code: str
    short_link_note: str | None
    target_url: str | None
    result: str
    block_reason: str | None
    block_detail: str | None
    ip: str | None
    country_code: str | None
    referer: str | None
    ua_raw: str | None
    ua_browser: str | None
    ua_browser_version: str | None
    ua_os: str | None
    ua_os_version: str | None
    ua_device_type: str | None
    ua_device_brand: str | None
    ua_device_model: str | None
    ua_bot_name: str | None
    accessed_at: datetime


async def write_access_log(session_factory, snapshot: AccessLogSnapshot) -> None:
    """Persist a pre-built snapshot without allowing database errors to affect redirects."""
    try:
        async with session_factory() as session:
            try:
                session.add(AccessLog(**asdict(snapshot)))
                await session.commit()
            except SQLAlchemyError:
                await session.rollback()
                logger.exception("failed to persist redirect access log")
    except SQLAlchemyError:
        logger.exception("failed to open session for redirect access log")
