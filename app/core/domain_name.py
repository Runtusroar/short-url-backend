"""Canonical validation for DNS domain names used by tenants and request hosts."""

from __future__ import annotations

from ipaddress import ip_address


def normalize_dns_hostname(value: str) -> str:
    """Return a lowercase ASCII DNS hostname without one optional trailing dot.

    This intentionally validates only DNS hostnames: stored tenant domains and
    the Host values used to select them must never be IP literals or arbitrary
    URI authority syntax.
    """
    if not isinstance(value, str):
        raise ValueError("域名必须是字符串")
    if value != value.strip() or not value.isascii():
        raise ValueError("域名必须是有效的 ASCII DNS 主机名")
    # ASCII-only input makes case normalization deterministic and prevents a
    # Unicode case fold (for example K -> k) from bypassing the ASCII policy.
    hostname = value.lower()
    if hostname.endswith("."):
        hostname = hostname[:-1]
    if not hostname or len(hostname) > 253:
        raise ValueError("域名必须是有效的 ASCII DNS 主机名")
    try:
        ip_address(hostname)
    except ValueError:
        pass
    else:
        raise ValueError("域名不能是 IP 地址")

    labels = hostname.split(".")
    if any(
        not label
        or len(label) > 63
        or label[0] == "-"
        or label[-1] == "-"
        or not all(character.isalnum() or character == "-" for character in label)
        for label in labels
    ):
        raise ValueError("域名必须是有效的 ASCII DNS 主机名")
    return hostname
