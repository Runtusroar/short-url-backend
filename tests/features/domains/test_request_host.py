from fastapi import Request

from app.features.domains.dependencies import get_request_host


def _request(host: str) -> Request:
    return Request({"type": "http", "headers": [(b"host", host.encode())]})


def test_request_host_normalizes_ports_ipv6_and_trailing_dot():
    assert get_request_host(_request("Example.TEST.:443")) == "example.test"
    assert get_request_host(_request("192.0.2.9:8080")) == "192.0.2.9"
    assert get_request_host(_request("[2001:DB8::1]:443")) == "2001:db8::1"
    assert get_request_host(_request("2001:DB8::1")) == "2001:db8::1"
