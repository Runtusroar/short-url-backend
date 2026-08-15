from starlette.requests import Request

from app.core.client_ip import get_client_ip


def make_request(client_ip="203.0.113.10", x_real_ip=None):
    headers = []
    if x_real_ip is not None:
        headers.append((b"x-real-ip", x_real_ip.encode()))
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": headers,
            "client": (client_ip, 12345),
            "server": ("testserver", 80),
            "scheme": "http",
            "query_string": b"",
        }
    )


def test_untrusted_proxy_header_is_ignored():
    request = make_request(x_real_ip="198.51.100.7")
    assert get_client_ip(request, trust_proxy_headers=False) == "203.0.113.10"


def test_trusted_proxy_header_is_used():
    request = make_request(x_real_ip="198.51.100.7")
    assert get_client_ip(request, trust_proxy_headers=True) == "198.51.100.7"


def test_invalid_trusted_proxy_header_falls_back_to_socket():
    request = make_request(x_real_ip="not-an-ip")
    assert get_client_ip(request, trust_proxy_headers=True) == "203.0.113.10"


def test_ipv6_is_normalized():
    request = make_request(x_real_ip="2001:0db8:0:0:0:0:0:1")
    assert get_client_ip(request, trust_proxy_headers=True) == "2001:db8::1"
