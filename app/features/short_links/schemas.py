from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic_core import PydanticCustomError


def _contract_error(message: str) -> PydanticCustomError:
    return PydanticCustomError("unprocessable_entity", message)


class TargetUrlBase(BaseModel):
    name: str | None = Field(default=None, max_length=128)
    url: str
    url_type: str = Field(..., pattern="^(allowed|denied)$")
    weight: int = 1
    is_active: bool = True

    @field_validator("weight")
    @classmethod
    def validate_weight(cls, value: int) -> int:
        if value < 1:
            raise _contract_error("weight must be at least 1")
        return value


class TargetUrlCreate(TargetUrlBase):
    pass


class TargetUrlUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=128)
    url: str | None = None
    url_type: str | None = Field(default=None, pattern="^(allowed|denied)$")
    weight: int | None = None
    is_active: bool | None = None

    @field_validator("weight")
    @classmethod
    def validate_weight(cls, value: int | None) -> int | None:
        if value is not None and value < 1:
            raise _contract_error("weight must be at least 1")
        return value


class TargetUrlResponse(TargetUrlBase):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    short_link_id: UUID
    created_at: datetime
    updated_at: datetime


class AccessRuleBase(BaseModel):
    action: str = Field(..., pattern="^(allow|deny)$")
    priority: int = 0
    countries: list[str] = Field(default_factory=list)
    ua_platforms: list[str] = Field(default_factory=list)
    referer_pattern: str | None = None
    allow_proxy: bool = True
    allow_bot: bool = True
    is_active: bool = True


class AccessRuleCreate(AccessRuleBase):
    pass


class AccessRuleUpdate(BaseModel):
    action: str | None = Field(default=None, pattern="^(allow|deny)$")
    priority: int | None = None
    countries: list[str] | None = None
    ua_platforms: list[str] | None = None
    referer_pattern: str | None = None
    allow_proxy: bool | None = None
    allow_bot: bool | None = None
    is_active: bool | None = None


class AccessRuleResponse(AccessRuleBase):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    short_link_id: UUID


class ShortLinkCreate(BaseModel):
    domain_id: UUID
    custom_alias: str | None = Field(default=None, pattern="^[a-zA-Z0-9_-]{3,32}$")
    name: str
    normal_urls: list[str] = Field(default_factory=list, min_length=1)
    blocked_urls: list[str] = Field(default_factory=list, min_length=1)

    @model_validator(mode="before")
    @classmethod
    def validate_name(cls, value: object) -> object:
        if not isinstance(value, dict):
            raise _contract_error("name must be 1 to 128 characters")
        name = value.get("name")
        if not isinstance(name, str) or not 1 <= len(name) <= 128:
            raise _contract_error("name must be 1 to 128 characters")
        return value


class ShortLinkUpdate(BaseModel):
    name: str | None = None
    is_active: bool | None = None

    @model_validator(mode="before")
    @classmethod
    def validate_name(cls, value: object) -> object:
        if not isinstance(value, dict) or "name" not in value:
            return value
        name = value["name"]
        if not isinstance(name, str) or not 1 <= len(name) <= 128:
            raise _contract_error("name must be 1 to 128 characters")
        return value


class ShortLinkResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    short_code: str
    is_custom_alias: bool
    name: str
    owner_id: UUID
    domain_id: UUID
    is_active: bool
    default_action: str
    created_at: datetime
    updated_at: datetime


class ShortLinkPermissionCreate(BaseModel):
    user_id: UUID


class ShortLinkPermissionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    short_link_id: UUID
    user_id: UUID
    granted_by: UUID | None
    created_at: datetime


class ShortLinkDetail(ShortLinkResponse):
    target_urls: list[TargetUrlResponse]
    access_rules: list[AccessRuleResponse]
    permissions: list[ShortLinkPermissionResponse] = []
