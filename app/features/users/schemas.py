from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class UserBase(BaseModel):
    username: str = Field(..., min_length=3, max_length=64)


class UserCreate(UserBase):
    password: str = Field(..., min_length=6)
    role: str = Field(..., pattern="^(admin|operator|client)$")
    domain_ids: list[UUID] = Field(default_factory=list)


class UserResponse(UserBase):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    role: str
    is_active: bool
    created_at: datetime
