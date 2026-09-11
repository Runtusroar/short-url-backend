import ipaddress
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


def normalize_ip_address(value: str | None) -> str | None:
    if value is None:
        return None
    if "%" in value:
        raise ValueError("IP 地址不能包含作用域")
    try:
        return str(ipaddress.ip_address(value.strip()))
    except ValueError as exc:
        raise ValueError("IP 地址格式不正确") from exc


class IpBlacklistCreate(BaseModel):
    ip: str = Field(min_length=1, max_length=45)
    reason: str | None = Field(default=None, max_length=1000)

    _normalize_ip = field_validator("ip")(normalize_ip_address)


class IpBlacklistUpdate(BaseModel):
    ip: str | None = Field(default=None, min_length=1, max_length=45)
    reason: str | None = Field(default=None, max_length=1000)

    _normalize_ip = field_validator("ip")(normalize_ip_address)


class IpBlacklistResponse(BaseModel):
    id: UUID
    ip: str
    reason: str | None
    created_by: UUID | None
    created_by_username: str | None
    created_at: datetime
