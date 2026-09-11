"""Extract one trustworthy, immutable request snapshot for redirect decisions."""

from __future__ import annotations

from dataclasses import dataclass
from ipaddress import ip_address

from fastapi import Request

from app.core.config import settings
from app.services.geoip import get_country
from app.services.user_agent import ParsedUserAgent, parse_user_agent


@dataclass(frozen=True, slots=True)
class RequestMetadata:
    ip: str | None
    request_url: str
    country: str | None
    referer: str | None
    ua: ParsedUserAgent


def _first_value(value: str | None) -> str | None:
    if not value:
        return None
    first = value.split(",", 1)[0].strip()
    return first or None


def _validated_ip(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return str(ip_address(value.strip()))
    except ValueError:
        return None


def request_host(request: Request) -> str:
    """Return the routing host, accepting forwarded values only from a trusted proxy."""
    forwarded_host = _first_value(request.headers.get("x-forwarded-host"))
    host = forwarded_host if settings.trust_proxy_headers and forwarded_host else request.headers.get("host", "localhost")
    return host.lower()


def _request_scheme(request: Request) -> str:
    forwarded_scheme = _first_value(request.headers.get("x-forwarded-proto"))
    if settings.trust_proxy_headers and forwarded_scheme:
        return forwarded_scheme.lower()
    return request.url.scheme


def extract_request_metadata(request: Request) -> RequestMetadata:
    """Normalize client metadata once so policy and audit fields cannot diverge."""
    if settings.trust_proxy_headers:
        ip = _validated_ip(request.headers.get("x-real-ip"))
        if ip is None:
            ip = _validated_ip(_first_value(request.headers.get("x-forwarded-for")))
    else:
        ip = _validated_ip(request.client.host if request.client else None)

    scheme = _request_scheme(request)
    host = request_host(request)
    query = f"?{request.url.query}" if request.url.query else ""
    request_url = f"{scheme}://{host}{request.url.path}{query}"
    ua_raw = request.headers.get("user-agent")
    ua = parse_user_agent(ua_raw, request.headers)
    return RequestMetadata(
        ip=ip,
        request_url=request_url,
        country=get_country(ip) if ip is not None else None,
        referer=request.headers.get("referer"),
        ua=ua,
    )
