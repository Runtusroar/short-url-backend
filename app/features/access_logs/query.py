import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date
from typing import Sequence
from uuid import UUID

import pycountry

from app.features.short_links.short_code import normalize_short_code
from app.models.enums import RedirectResult
from app.models.user import User


SHORT_CODE_PREFIX_RE = re.compile(r"^[a-z0-9_-]{1,32}$")


def normalize_access_log_name(value: str | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("name must be 1 to 128 characters")
    normalized = value.strip()
    if not normalized or len(normalized) > 128:
        raise ValueError("name must be 1 to 128 characters")
    return normalized


def normalize_short_code_prefix(value: str | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("short_code must use lowercase URL-safe characters")
    normalized = normalize_short_code(value)
    if not SHORT_CODE_PREFIX_RE.fullmatch(normalized):
        raise ValueError("short_code must use lowercase URL-safe characters")
    return normalized


def normalize_access_log_countries(values: Sequence[str]) -> tuple[str, ...]:
    normalized = []
    for value in values:
        if not isinstance(value, str):
            raise ValueError("countries must contain ISO alpha-2 codes")
        country_code = value.strip().upper()
        if not pycountry.countries.get(alpha_2=country_code):
            raise ValueError("countries must contain ISO alpha-2 codes")
        normalized.append(country_code)
    return tuple(sorted(set(normalized)))


def normalize_access_log_results(
    values: Sequence[RedirectResult | str],
) -> tuple[RedirectResult, ...]:
    normalized = []
    for value in values:
        if isinstance(value, RedirectResult):
            normalized.append(value)
            continue
        if not isinstance(value, str):
            raise ValueError("results must contain valid redirect results")
        try:
            normalized.append(RedirectResult(value.strip().lower()))
        except ValueError as exc:
            raise ValueError("results must contain valid redirect results") from exc
    return tuple(sorted(set(normalized), key=lambda result: result.value))


@dataclass(frozen=True)
class AccessLogFilters:
    short_link_id: UUID | None
    short_code: str | None
    name: str | None
    date_from: date | None
    date_to: date | None
    countries: tuple[str, ...]
    results: tuple[RedirectResult, ...]

    @classmethod
    def from_values(
        cls,
        *,
        short_link_id: UUID | None,
        short_code: str | None,
        name: str | None,
        date_from: date | None,
        date_to: date | None,
        countries: Sequence[str],
        results: Sequence[RedirectResult | str],
    ) -> "AccessLogFilters":
        if date_from and date_to and date_from > date_to:
            raise ValueError("date_from must be before or equal to date_to")
        return cls(
            short_link_id=short_link_id,
            short_code=normalize_short_code_prefix(short_code),
            name=normalize_access_log_name(name),
            date_from=date_from,
            date_to=date_to,
            countries=normalize_access_log_countries(countries),
            results=normalize_access_log_results(results),
        )

    def digest_scope(self, effective_domain_id: UUID, current_user: User) -> str:
        payload = {
            "v": 1,
            "domain_id": str(effective_domain_id),
            "user_id": str(current_user.id),
            "role": str(current_user.role),
            "short_link_id": str(self.short_link_id) if self.short_link_id else None,
            "short_code": self.short_code,
            "name": self.name,
            "date_from": self.date_from.isoformat() if self.date_from else None,
            "date_to": self.date_to.isoformat() if self.date_to else None,
            "countries": list(self.countries),
            "results": [value.value for value in self.results],
        }
        encoded = json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()
