from datetime import date, datetime
from uuid import UUID

import pycountry
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    IPvAnyAddress,
    field_validator,
    model_validator,
)
from pydantic_core import PydanticCustomError

from app.features.access_logs.query import (
    AccessLogFilters,
    normalize_access_log_name,
    normalize_access_log_results,
    normalize_short_code_prefix,
)
from app.models.enums import RedirectResult


def _filter_error(message: str) -> PydanticCustomError:
    return PydanticCustomError("unprocessable_entity", message)


class AccessLogFilterParams(BaseModel):
    short_link_id: UUID | None = None
    short_code: str | None = None
    name: str | None = Field(default=None, max_length=128)
    date_from: date | None = None
    date_to: date | None = None
    countries: list[str] = Field(default_factory=list)
    results: list[RedirectResult] = Field(default_factory=list)

    @field_validator("short_code", mode="before")
    @classmethod
    def validate_short_code(cls, value: object) -> object:
        try:
            return normalize_short_code_prefix(value)  # type: ignore[arg-type]
        except ValueError as exc:
            raise _filter_error(str(exc)) from exc

    @field_validator("name", mode="before")
    @classmethod
    def validate_name(cls, value: object) -> object:
        try:
            return normalize_access_log_name(value)  # type: ignore[arg-type]
        except ValueError as exc:
            raise _filter_error(str(exc)) from exc

    @field_validator("countries", mode="before")
    @classmethod
    def validate_countries(cls, values: object) -> list[str]:
        if not isinstance(values, list):
            raise _filter_error("countries must contain ISO alpha-2 codes")
        normalized = []
        for value in values:
            if not isinstance(value, str):
                raise _filter_error("countries must contain ISO alpha-2 codes")
            country_code = value.strip().upper()
            if not pycountry.countries.get(alpha_2=country_code):
                raise _filter_error("countries must contain ISO alpha-2 codes")
            normalized.append(country_code)
        return normalized

    @field_validator("results", mode="before")
    @classmethod
    def validate_results(cls, values: object) -> list[RedirectResult | str]:
        if not isinstance(values, list):
            raise _filter_error("results must contain valid redirect results")
        try:
            return [result.value for result in normalize_access_log_results(values)]
        except ValueError as exc:
            raise _filter_error("results must contain valid redirect results") from exc

    @model_validator(mode="after")
    def validate_date_range(self) -> "AccessLogFilterParams":
        if self.date_from and self.date_to and self.date_from > self.date_to:
            raise _filter_error("date_from must be before or equal to date_to")
        return self

    def to_filters(self) -> AccessLogFilters:
        return AccessLogFilters.from_values(
            short_link_id=self.short_link_id,
            short_code=self.short_code,
            name=self.name,
            date_from=self.date_from,
            date_to=self.date_to,
            countries=self.countries,
            results=self.results,
        )


class AccessLogResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    short_link_id: UUID
    domain_id: UUID
    target_url_id: UUID | None
    result: str
    client_ip: IPvAnyAddress | None
    country: str | None
    user_agent: str | None
    ua_platform: str | None
    referer: str | None
    accessed_at: datetime
    access_date: date
    decision_reason: str
    matched_rule_id: UUID | None
    matched_rule_name: str | None
    target_url_snapshot: str | None
    request_host: str | None
    request_method: str
    proxy_check_status: str
    is_anonymous: bool | None
    proxy_types: list
    proxy_source: str | None


class AccessLogItemResponse(AccessLogResponse):
    short_code: str
    short_link_name: str


class AccessLogPageResponse(BaseModel):
    items: list[AccessLogItemResponse]
    next_cursor: str | None
    has_more: bool


class DailyStatsResponse(BaseModel):
    date: str
    total: int
    allowed: int
    denied: int
    unique_ips: int
