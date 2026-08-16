from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, IPvAnyAddress


class AccessLogResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    short_link_id: UUID
    domain_id: UUID
    target_url_id: UUID | None
    result: str
    client_ip: IPvAnyAddress | None
    country: str | None
    user_agent: str | None
    ua_platform: str | None
    referer: str | None
    accessed_at: datetime
    access_date: date
    decision_reason: str
    matched_rule_id: UUID | None
    matched_rule_name: str | None
    target_url_snapshot: str | None
    request_host: str | None
    request_method: str
    proxy_check_status: str
    is_anonymous: bool | None
    proxy_types: list
    proxy_source: str | None


class DailyStatsResponse(BaseModel):
    date: str
    total: int
    allowed: int
    denied: int
    unique_ips: int
