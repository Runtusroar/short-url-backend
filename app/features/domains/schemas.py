from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class DomainBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    is_active: bool = True
    is_default: bool = False


class DomainCreate(DomainBase):
    pass


class DomainUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    is_active: bool | None = None
    is_default: bool | None = None


class DomainResponse(DomainBase):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    created_at: datetime
