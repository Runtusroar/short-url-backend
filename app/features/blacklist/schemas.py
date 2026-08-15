from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class IpBlacklistCreate(BaseModel):
    ip: str
    reason: str | None = None


class IpBlacklistResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    ip: str
    reason: str | None
    created_by: UUID | None
    created_at: datetime
