from ipaddress import ip_address

from fastapi import Request

from app.config import settings


def _normalize_ip(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return str(ip_address(value.strip()))
    except ValueError:
        return None


def get_client_ip(
    request: Request,
    trust_proxy_headers: bool | None = None,
) -> str:
    trust_headers = (
        settings.trust_proxy_headers
        if trust_proxy_headers is None
        else trust_proxy_headers
    )
    if trust_headers:
        forwarded_ip = _normalize_ip(request.headers.get("x-real-ip"))
        if forwarded_ip:
            return forwarded_ip
    socket_ip = _normalize_ip(request.client.host if request.client else None)
    return socket_ip or "unknown"
