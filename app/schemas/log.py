"""Access-log search response contracts."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel

from app.db.models import AccessResult, BlockReason


class AccessLogResponse(BaseModel):
    id: UUID
    short_link_id: UUID | None
    domain_id: UUID | None
    target_url_id: UUID | None
    request_url: str | None
    domain_name: str | None
    short_code: str | None
    short_link_note: str | None
    target_url: str | None
    result: AccessResult
    block_reason: BlockReason | None
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


class AccessLogCursorPage(BaseModel):
    items: list[AccessLogResponse]
    next_cursor: str | None
