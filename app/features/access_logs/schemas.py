from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class AccessLogResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    short_link_id: UUID
    domain_id: UUID
    target_url_id: UUID | None
    result: str
    ip: str
    country: str | None
    ua_platform: str | None
    referer: str | None
    accessed_at: datetime


class DailyStatsResponse(BaseModel):
    date: str
    total: int
    allowed: int
    denied: int
    unique_ips: int
