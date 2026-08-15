from datetime import datetime
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator


def normalize_domain_name(value: str) -> str:
    normalized = value.strip().lower()
    if not normalized or len(normalized) > 253:
        raise ValueError("域名格式不正确")
    labels = normalized.split(".")
    if any(
        not label
        or len(label) > 63
        or label[0] == "-"
        or label[-1] == "-"
        or not all(char.isascii() and (char.isalnum() or char == "-") for char in label)
        for label in labels
    ):
        raise ValueError("域名格式不正确")
    return normalized


def validate_timezone(value: str) -> str:
    try:
        ZoneInfo(value)
    except (TypeError, ZoneInfoNotFoundError) as exc:
        raise ValueError("时区必须是有效的 IANA 时区") from exc
    return value


class DomainBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    timezone: str = Field(default="Asia/Shanghai", max_length=64)
    is_active: bool = True
    is_default: bool = False

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        return normalize_domain_name(value)

    @field_validator("timezone")
    @classmethod
    def validate_timezone_value(cls, value: str) -> str:
        return validate_timezone(value)


class DomainCreate(DomainBase):
    pass


class DomainUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    timezone: str | None = Field(default=None, max_length=64)
    is_active: bool | None = None
    is_default: bool | None = None

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str | None) -> str | None:
        return normalize_domain_name(value) if value is not None else None

    @field_validator("timezone")
    @classmethod
    def validate_timezone_value(cls, value: str | None) -> str | None:
        return validate_timezone(value) if value is not None else None


class DomainResponse(DomainBase):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    created_at: datetime
    updated_at: datetime
