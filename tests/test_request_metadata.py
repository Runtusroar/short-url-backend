from starlette.requests import Request

from app.routers.redirect import _get_client_ip, _is_proxy


def _request(*, headers: dict[str, str] | None = None, client_ip: str = "172.25.0.1") -> Request:
    encoded_headers = [
        (name.lower().encode("latin-1"), value.encode("latin-1"))
        for name, value in (headers or {}).items()
    ]
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": "/test",
            "raw_path": b"/test",
            "query_string": b"",
            "headers": encoded_headers,
            "client": (client_ip, 12345),
            "server": ("testserver", 80),
        }
    )


def test_get_client_ip_prefers_nginx_sanitized_real_ip_over_forwarded_chain():
    request = _request(
        headers={
            "X-Real-IP": "203.0.113.24",
            "X-Forwarded-For": "198.51.100.77, 172.64.1.10",
        }
    )

    assert _get_client_ip(request) == "203.0.113.24"


def test_get_client_ip_accepts_ipv6_real_ip():
    request = _request(headers={"X-Real-IP": "2001:db8:0:1::42"})

    assert _get_client_ip(request) == "2001:db8:0:1::42"


def test_get_client_ip_ignores_invalid_forwarded_values():
    request = _request(
        headers={
            "X-Real-IP": "not-an-ip",
            "X-Forwarded-For": "also-not-an-ip, 172.64.1.10",
        },
        client_ip="172.25.0.1",
    )

    assert _get_client_ip(request) == "172.25.0.1"


def test_reverse_proxy_headers_do_not_mean_the_client_uses_a_vpn():
    request = _request(
        headers={
            "X-Real-IP": "203.0.113.24",
            "X-Forwarded-For": "203.0.113.24",
            "Via": "cloudflare",
        }
    )

    assert _is_proxy(request) is False
