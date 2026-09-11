from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator

from app.db.models import PolicyMode, TargetUrlType
from app.services.short_code import validate_custom_alias


class DestinationWrite(BaseModel):
    id: UUID | None = None
    url: str = Field(min_length=1, max_length=4096)
    type: TargetUrlType
    weight: int = Field(default=1, ge=0)
    is_active: bool = True


class DestinationResponse(BaseModel):
    id: UUID
    url: str
    type: TargetUrlType
    weight: int
    is_active: bool
    created_at: datetime


class LinkPolicyWrite(BaseModel):
    country_mode: PolicyMode = PolicyMode.OFF
    countries: list[str] = Field(default_factory=list)
    platform_mode: PolicyMode = PolicyMode.OFF
    platforms: list[str] = Field(default_factory=list)
    referer_mode: PolicyMode = PolicyMode.OFF
    referer_patterns: list[str] = Field(default_factory=list)
    block_proxy: bool = False
    block_bot: bool = False

    @field_validator("countries")
    @classmethod
    def normalize_countries(cls, countries: list[str]) -> list[str]:
        normalized = [country.strip().upper() for country in countries]
        if any(not country for country in normalized):
            raise ValueError("国家代码不能为空")
        return normalized

    @field_validator("platforms")
    @classmethod
    def normalize_platforms(cls, platforms: list[str]) -> list[str]:
        normalized = [platform.strip().lower() for platform in platforms]
        if any(not platform for platform in normalized):
            raise ValueError("平台不能为空")
        return normalized

    @field_validator("referer_patterns")
    @classmethod
    def normalize_referer_patterns(cls, patterns: list[str]) -> list[str]:
        normalized = [pattern.strip().lower() for pattern in patterns]
        if any(not pattern for pattern in normalized):
            raise ValueError("Referer 域名模式不能为空")
        return normalized

    @model_validator(mode="after")
    def allow_modes_need_values(self) -> "LinkPolicyWrite":
        if self.country_mode == PolicyMode.ALLOW and not self.countries:
            raise ValueError("国家允许模式必须指定国家")
        if self.platform_mode == PolicyMode.ALLOW and not self.platforms:
            raise ValueError("平台允许模式必须指定平台")
        if self.referer_mode == PolicyMode.ALLOW and not self.referer_patterns:
            raise ValueError("Referer 允许模式必须指定域名模式")
        return self


class LinkPolicyResponse(LinkPolicyWrite):
    updated_at: datetime | None


class ShortLinkWrite(BaseModel):
    domain_id: UUID
    custom_alias: str | None = Field(default=None, min_length=3, max_length=32)
    note: str | None = Field(default=None, max_length=4000)
    is_active: bool = True
    destinations: list[DestinationWrite] = Field(min_length=1)
    policy: LinkPolicyWrite

    @field_validator("custom_alias")
    @classmethod
    def validate_alias(cls, alias: str | None) -> str | None:
        if alias is not None and not validate_custom_alias(alias):
            raise ValueError("短码格式不正确或为保留字")
        return alias

    @model_validator(mode="after")
    def require_active_allowed_destination(self) -> "ShortLinkWrite":
        if not any(
            destination.type == TargetUrlType.ALLOWED and destination.is_active
            for destination in self.destinations
        ):
            raise ValueError("至少需要一个启用的允许目标 URL")
        ids = [destination.id for destination in self.destinations if destination.id is not None]
        if len(ids) != len(set(ids)):
            raise ValueError("目标 URL 不能重复")
        return self


class ShortLinkListItem(BaseModel):
    id: UUID
    domain_id: UUID
    short_code: str
    is_custom_alias: bool
    note: str | None
    owner_id: UUID | None
    is_active: bool
    created_at: datetime
    updated_at: datetime


class ShortLinkResponse(ShortLinkListItem):
    destinations: list[DestinationResponse]
    policy: LinkPolicyResponse
