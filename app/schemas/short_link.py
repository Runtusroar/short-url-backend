import re
from datetime import datetime
from ipaddress import ip_address
from uuid import UUID
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, field_validator, model_validator

from app.core.domain_name import normalize_dns_hostname
from app.db.models import Platform, PolicyMode, TargetUrlType
from app.services.short_code import validate_custom_alias


_COUNTRY_CODE_RE = re.compile(r"^[A-Za-z]{2}$", re.ASCII)
_HOST_LABEL_RE = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
_REFERER_PATTERN_RE = re.compile(
    rf"^(?:\*\.)?{_HOST_LABEL_RE}(?:\.{_HOST_LABEL_RE})+$", re.ASCII
)


def _validate_destination_url(value: str) -> str:
    if value != value.strip() or any(character.isspace() for character in value):
        raise ValueError("目标 URL 不能包含空白字符")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValueError("目标 URL 不能包含控制字符")
    try:
        parsed = urlsplit(value)
        if not parsed.netloc.isascii():
            raise ValueError
        hostname = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        raise ValueError("目标 URL 的主机或端口无效") from exc
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or hostname is None:
        raise ValueError("目标 URL 必须是绝对 http 或 https 地址")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("目标 URL 不能包含用户信息")
    if port is not None and not 1 <= port <= 65535:
        raise ValueError("目标 URL 端口无效")
    try:
        ip_address(hostname)
    except ValueError:
        try:
            normalize_dns_hostname(hostname)
        except ValueError as exc:
            raise ValueError("目标 URL 的主机无效") from exc
    return value


class DestinationWrite(BaseModel):
    id: UUID | None = None
    url: str = Field(min_length=1, max_length=4096)
    type: TargetUrlType
    weight: int = Field(default=1, ge=0)
    is_active: bool = True

    _validate_url = field_validator("url")(_validate_destination_url)


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
        if any(_COUNTRY_CODE_RE.fullmatch(country) is None for country in countries):
            raise ValueError("国家代码必须是两个 ASCII 字母")
        return [country.upper() for country in countries]

    @field_validator("platforms")
    @classmethod
    def normalize_platforms(cls, platforms: list[str]) -> list[str]:
        normalized = []
        for value in platforms:
            platform = value.strip().lower()
            try:
                Platform(platform)
            except ValueError as exc:
                raise ValueError("平台必须是运行时支持的设备类型")

            if platform not in normalized:
                normalized.append(platform)
        return normalized

    @field_validator("referer_patterns")
    @classmethod
    def normalize_referer_patterns(cls, patterns: list[str]) -> list[str]:
        if any(not pattern.isascii() for pattern in patterns):
            raise ValueError("Referer 必须是 ASCII 域名模式")
        normalized = [pattern.lower() for pattern in patterns]
        if any(
            len(pattern) > 253 or _REFERER_PATTERN_RE.fullmatch(pattern) is None
            for pattern in normalized
        ):
            raise ValueError("Referer 必须是域名或以 *. 开头的域名模式")
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
