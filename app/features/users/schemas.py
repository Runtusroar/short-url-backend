from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.enums import UserRole


def normalize_username(value: str) -> str:
    normalized = value.strip().lower()
    if not 3 <= len(normalized) <= 64:
        raise ValueError("用户名长度必须在 3 到 64 个字符之间")
    return normalized


class UserBase(BaseModel):
    username: str = Field(..., min_length=3, max_length=64)

    @field_validator("username")
    @classmethod
    def normalize_username_value(cls, value: str) -> str:
        return normalize_username(value)


class UserCreate(UserBase):
    password: str = Field(..., min_length=6)
    role: UserRole
    domain_ids: list[UUID] = Field(default_factory=list)


class UserUpdate(BaseModel):
    username: str | None = Field(default=None, min_length=3, max_length=64)
    password: str | None = Field(default=None, min_length=6)
    role: UserRole | None = None
    domain_ids: list[UUID] | None = None
    is_active: bool | None = None

    @field_validator("username")
    @classmethod
    def normalize_username_value(cls, value: str | None) -> str | None:
        return normalize_username(value) if value is not None else None


class UserResponse(UserBase):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    role: str
    is_active: bool
    created_at: datetime
