from datetime import datetime
from uuid import UUID

from pycountry import countries as iso_countries
from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic_core import PydanticCustomError

from app.models.enums import AccessAction, ClientRequirement, ProxyRequirement


def _contract_error(message: str) -> PydanticCustomError:
    return PydanticCustomError("unprocessable_entity", message)


ISO_3166_ALPHA_2_CODES = frozenset(country.alpha_2 for country in iso_countries)


def _normalize_rule_name(value: object) -> str:
    if not isinstance(value, str):
        raise _contract_error("name must be 1 to 128 characters")
    normalized = value.strip()
    if not normalized:
        raise _contract_error("name must be 1 to 128 characters")
    return normalized


def _normalize_countries(values: object) -> list[str]:
    if not isinstance(values, list):
        raise _contract_error("countries must contain ISO alpha-2 codes")
    normalized = []
    for value in values:
        if not isinstance(value, str):
            raise _contract_error("countries must contain ISO alpha-2 codes")
        country = value.strip().upper()
        if country not in ISO_3166_ALPHA_2_CODES:
            raise _contract_error("countries must contain ISO alpha-2 codes")
        normalized.append(country)
    return normalized


def _normalize_platforms(values: object) -> list[str]:
    if not isinstance(values, list):
        raise _contract_error("ua_platforms must contain non-empty strings")
    normalized = []
    for value in values:
        if not isinstance(value, str):
            raise _contract_error("ua_platforms must contain non-empty strings")
        if not (platform := value.strip()):
            raise _contract_error("ua_platforms must contain non-empty strings")
        normalized.append(platform.lower())
    return normalized


def _normalize_referer_patterns(values: object) -> list[str]:
    if not isinstance(values, list):
        raise _contract_error("referer_patterns must contain strings")
    normalized = []
    for value in values:
        if not isinstance(value, str):
            raise _contract_error("referer_patterns must contain strings")
        normalized.extend(part.strip() for part in value.split(",") if part.strip())
    return normalized


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
    name: str = Field(..., min_length=1, max_length=128)
    action: AccessAction
    priority: int | None = None
    countries: list[str] = Field(default_factory=list)
    ua_platforms: list[str] = Field(default_factory=list)
    referer_patterns: list[str] = Field(default_factory=list)
    client_requirement: ClientRequirement = ClientRequirement.ANY
    proxy_requirement: ProxyRequirement = ProxyRequirement.ANY
    is_active: bool = True

    @field_validator("name")
    @classmethod
    def validate_rule_name(cls, value: str) -> str:
        return _normalize_rule_name(value)

    @field_validator("countries")
    @classmethod
    def normalize_countries(cls, values: list[str]) -> list[str]:
        return _normalize_countries(values)

    @field_validator("ua_platforms")
    @classmethod
    def normalize_platforms(cls, values: list[str]) -> list[str]:
        return _normalize_platforms(values)

    @field_validator("referer_patterns")
    @classmethod
    def normalize_referer_patterns(cls, values: list[str]) -> list[str]:
        return _normalize_referer_patterns(values)


class AccessRuleCreate(AccessRuleBase):
    pass


class AccessRuleUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    action: AccessAction | None = None
    priority: int | None = None
    countries: list[str] | None = None
    ua_platforms: list[str] | None = None
    referer_patterns: list[str] | None = None
    client_requirement: ClientRequirement | None = None
    proxy_requirement: ProxyRequirement | None = None
    is_active: bool | None = None

    @field_validator("name")
    @classmethod
    def validate_rule_name(cls, value: str) -> str:
        return _normalize_rule_name(value)

    @field_validator("countries")
    @classmethod
    def normalize_countries(cls, values: list[str]) -> list[str]:
        return _normalize_countries(values)

    @field_validator("ua_platforms")
    @classmethod
    def normalize_platforms(cls, values: list[str]) -> list[str]:
        return _normalize_platforms(values)

    @field_validator("referer_patterns")
    @classmethod
    def normalize_referer_patterns(cls, values: list[str]) -> list[str]:
        return _normalize_referer_patterns(values)


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

    @field_validator("name", mode="before")
    @classmethod
    def validate_name(cls, value: object) -> str:
        if not isinstance(value, str) or not value.strip() or len(value) > 128:
            raise _contract_error("name must be 1 to 128 characters")
        return value


class ShortLinkUpdate(BaseModel):
    name: str | None = None
    is_active: bool | None = None

    @field_validator("name", mode="before")
    @classmethod
    def validate_name(cls, value: object) -> str:
        if not isinstance(value, str) or not value.strip() or len(value) > 128:
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
