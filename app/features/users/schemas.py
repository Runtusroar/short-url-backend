from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.enums import UserRole


def normalize_username(value: str) -> str:
    return value.strip().lower()


class UserBase(BaseModel):
    username: str = Field(..., min_length=3, max_length=64)

    @field_validator("username", mode="before")
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

    @field_validator("username", mode="before")
    @classmethod
    def normalize_username_value(cls, value: str | None) -> str | None:
        return normalize_username(value) if value is not None else None


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    username: str
    role: str
    is_active: bool
    created_at: datetime
