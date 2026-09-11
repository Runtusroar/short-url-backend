from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


def normalize_domain_name(value: str | None) -> str | None:
    if value is None:
        return None
    name = value.strip().lower().rstrip(".")
    if not name or any(character in name for character in ("://", "/", "?", "#", ":")):
        raise ValueError("域名不能包含协议、路径、查询参数或端口")
    return name


class DomainCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    is_active: bool = True

    _normalize_name = field_validator("name")(normalize_domain_name)


class DomainUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    is_active: bool | None = None

    _normalize_name = field_validator("name")(normalize_domain_name)


class DomainResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    is_active: bool
    created_at: datetime
