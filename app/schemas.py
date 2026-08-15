from datetime import datetime
from uuid import UUID
from pydantic import BaseModel, Field, ConfigDict


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


class DomainBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    is_active: bool = True
    is_default: bool = False


class DomainCreate(DomainBase):
    pass


class DomainUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    is_active: bool | None = None
    is_default: bool | None = None


class DomainResponse(DomainBase):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    created_at: datetime


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


class TargetUrlBase(BaseModel):
    url: str
    url_type: str = Field(..., pattern="^(allowed|denied)$")
    weight: int = Field(default=1, ge=0)
    is_active: bool = True


class TargetUrlCreate(TargetUrlBase):
    pass


class TargetUrlUpdate(BaseModel):
    url: str | None = None
    url_type: str | None = Field(default=None, pattern="^(allowed|denied)$")
    weight: int | None = Field(default=None, ge=0)
    is_active: bool | None = None


class TargetUrlResponse(TargetUrlBase):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    short_link_id: UUID
    created_at: datetime


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
    is_active: bool | None = None


class AccessRuleResponse(AccessRuleBase):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    short_link_id: UUID


class ShortLinkBase(BaseModel):
    short_code: str | None = Field(default=None, pattern="^[a-zA-Z0-9_-]{3,32}$")
    description: str | None = None
    is_active: bool = True


class ShortLinkCreate(BaseModel):
    domain_id: UUID
    custom_alias: str | None = Field(default=None, pattern="^[a-zA-Z0-9_-]{3,32}$")
    description: str | None = None
    normal_urls: list[str] = Field(default_factory=list, min_length=1)
    blocked_urls: list[str] = Field(default_factory=list, min_length=1)


class ShortLinkUpdate(BaseModel):
    description: str | None = None
    is_active: bool | None = None


class ShortLinkResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    short_code: str
    is_custom_alias: bool
    description: str | None
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
    created_at: datetime


class ShortLinkDetail(ShortLinkResponse):
    target_urls: list[TargetUrlResponse]
    access_rules: list[AccessRuleResponse]
    permissions: list[ShortLinkPermissionResponse] = []


class AccessLogResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    short_link_id: UUID
    domain_id: UUID
    target_url_id: UUID | None
    result: str
    ip: str
    country: str | None
    ua_platform: str | None
    referer: str | None
    accessed_at: datetime


class DailyStatsResponse(BaseModel):
    date: str
    total: int
    allowed: int
    denied: int
    unique_ips: int


class IpBlacklistCreate(BaseModel):
    ip: str
    reason: str | None = None


class IpBlacklistResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    ip: str
    reason: str | None
    created_by: UUID | None
    created_at: datetime
