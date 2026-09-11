"""Extract one trustworthy, immutable request snapshot for redirect decisions."""

from __future__ import annotations

from dataclasses import dataclass
from ipaddress import IPv6Address, ip_address

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


@dataclass(frozen=True, slots=True)
class RequestAuthority:
    host: str
    authority: str


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


def _valid_port(value: str) -> int | None:
    if not value or not value.isascii() or not value.isdigit():
        return None
    port = int(value)
    return port if 1 <= port <= 65535 else None


def _canonical_dns_or_ipv4(value: str) -> str | None:
    host = value[:-1] if value.endswith(".") else value
    if not host or host.endswith("."):
        return None
    try:
        parsed_ip = ip_address(host)
    except ValueError:
        pass
    else:
        return str(parsed_ip) if not isinstance(parsed_ip, IPv6Address) else None

    labels = host.split(".")
    if any(
        not label
        or len(label) > 63
        or label[0] == "-"
        or label[-1] == "-"
        or not all(character.isascii() and (character.isalnum() or character == "-") for character in label)
        for label in labels
    ):
        return None
    canonical = host.lower()
    return canonical if len(canonical) <= 253 else None


def parse_authority(value: str | None) -> RequestAuthority | None:
    """Parse only a legal HTTP authority, preserving no attacker-controlled syntax."""
    if not value or value != value.strip() or any(character in value for character in "@/?#\\"):
        return None

    if value.startswith("["):
        close = value.find("]")
        if close < 0 or value.count("]") != 1:
            return None
        literal = value[1:close]
        suffix = value[close + 1 :]
        if suffix and not suffix.startswith(":"):
            return None
        try:
            parsed_ip = ip_address(literal)
        except ValueError:
            return None
        if not isinstance(parsed_ip, IPv6Address):
            return None
        port = _valid_port(suffix[1:]) if suffix else None
        if suffix and port is None:
            return None
        host = str(parsed_ip)
        authority = f"[{host}]" + (f":{port}" if port is not None else "")
        return RequestAuthority(host, authority)

    if "[" in value or "]" in value or value.count(":") > 1:
        return None
    host_part, separator, port_part = value.partition(":")
    port = _valid_port(port_part) if separator else None
    if separator and port is None:
        return None
    host = _canonical_dns_or_ipv4(host_part)
    if host is None:
        return None
    authority = host + (f":{port}" if port is not None else "")
    return RequestAuthority(host, authority)


def request_authority(request: Request) -> RequestAuthority | None:
    """Return the trusted routing authority, or None for malformed input."""
    forwarded_host = _first_value(request.headers.get("x-forwarded-host"))
    value = forwarded_host if settings.trust_proxy_headers and forwarded_host else request.headers.get("host", "localhost")
    return parse_authority(value)


def request_host(request: Request) -> str | None:
    authority = request_authority(request)
    return authority.host if authority is not None else None


def _request_scheme(request: Request) -> str:
    forwarded_scheme = _first_value(request.headers.get("x-forwarded-proto"))
    if settings.trust_proxy_headers and forwarded_scheme:
        return forwarded_scheme.lower()
    return str(request.scope.get("scheme", "http"))


def extract_request_metadata(request: Request) -> RequestMetadata:
    """Normalize client metadata once so policy and audit fields cannot diverge."""
    if settings.trust_proxy_headers:
        ip = _validated_ip(request.headers.get("x-real-ip"))
        if ip is None:
            ip = _validated_ip(_first_value(request.headers.get("x-forwarded-for")))
    else:
        ip = _validated_ip(request.client.host if request.client else None)

    scheme = _request_scheme(request)
    authority = request_authority(request)
    host = authority.authority if authority is not None else ""
    query_string = request.scope.get("query_string", b"")
    query = f"?{query_string.decode('latin-1')}" if query_string else ""
    request_url = f"{scheme}://{host}{request.scope.get('path', '')}{query}"
    ua_raw = request.headers.get("user-agent")
    ua = parse_user_agent(ua_raw, request.headers)
    return RequestMetadata(
        ip=ip,
        request_url=request_url,
        country=get_country(ip) if ip is not None else None,
        referer=request.headers.get("referer"),
        ua=ua,
    )
