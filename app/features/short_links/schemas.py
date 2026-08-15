from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic_core import PydanticCustomError

from app.models.enums import AccessAction, ClientRequirement, ProxyRequirement


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
        normalized = value.strip()
        if not normalized:
            raise _contract_error("name must be 1 to 128 characters")
        return normalized

    @field_validator("countries")
    @classmethod
    def normalize_countries(cls, values: list[str]) -> list[str]:
        normalized = []
        for value in values:
            if not isinstance(value, str):
                raise _contract_error("countries must contain ISO alpha-2 codes")
            country = value.strip().upper()
            if len(country) != 2 or not country.isalpha():
                raise _contract_error("countries must contain ISO alpha-2 codes")
            normalized.append(country)
        return normalized

    @field_validator("ua_platforms")
    @classmethod
    def normalize_platforms(cls, values: list[str]) -> list[str]:
        normalized = []
        for value in values:
            if not isinstance(value, str) or not (platform := value.strip()):
                raise _contract_error("ua_platforms must contain non-empty strings")
            normalized.append(platform.lower())
        return normalized

    @field_validator("referer_patterns")
    @classmethod
    def normalize_referer_patterns(cls, values: list[str]) -> list[str]:
        normalized = []
        for value in values:
            if not isinstance(value, str):
                raise _contract_error("referer_patterns must contain strings")
            normalized.extend(part.strip() for part in value.split(",") if part.strip())
        return normalized


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

    _validate_rule_name = field_validator("name")(AccessRuleBase.validate_rule_name)
    _normalize_countries = field_validator("countries")(
        AccessRuleBase.normalize_countries
    )
    _normalize_platforms = field_validator("ua_platforms")(
        AccessRuleBase.normalize_platforms
    )
    _normalize_referer_patterns = field_validator("referer_patterns")(
        AccessRuleBase.normalize_referer_patterns
    )


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
