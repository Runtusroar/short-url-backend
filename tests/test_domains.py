from starlette.requests import Request

from app.domains import _get_host


def make_request(host="public.example", x_forwarded_host=None):
    headers = [(b"host", host.encode())]
    if x_forwarded_host is not None:
        headers.append((b"x-forwarded-host", x_forwarded_host.encode()))
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": headers,
            "client": ("203.0.113.10", 12345),
            "server": ("testserver", 80),
            "scheme": "http",
            "query_string": b"",
        }
    )


def test_domain_resolution_ignores_x_forwarded_host():
    request = make_request(x_forwarded_host="attacker.example")

    assert _get_host(request) == "public.example"
