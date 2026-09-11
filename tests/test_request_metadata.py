from starlette.requests import Request

from app.services.request_metadata import extract_request_metadata


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
            "path": "/CampaignA",
            "raw_path": b"/CampaignA",
            "query_string": b"source=test",
            "headers": encoded_headers,
            "client": (client_ip, 12345),
            "server": ("origin.example", 80),
        }
    )


def test_trusted_proxy_headers_define_the_snapshotted_client_and_request_url(monkeypatch):
    """Ignoring trusted forwarding would log the proxy instead of the visitor."""
    monkeypatch.setattr("app.services.request_metadata.settings.trust_proxy_headers", True)
    request = _request(
        headers={
            "Host": "origin.example",
            "X-Real-IP": "203.0.113.24",
            "X-Forwarded-For": "198.51.100.77, 172.64.1.10",
            "X-Forwarded-Proto": "https",
            "X-Forwarded-Host": "a.example",
            "Referer": "https://partner.example/article",
            "User-Agent": "Googlebot/2.1 (+http://www.google.com/bot.html)",
        }
    )

    metadata = extract_request_metadata(request)

    assert metadata.ip == "203.0.113.24"
    assert metadata.request_url == "https://a.example/CampaignA?source=test"
    assert metadata.country is None
    assert metadata.referer == "https://partner.example/article"
    assert metadata.ua.raw == "Googlebot/2.1 (+http://www.google.com/bot.html)"


def test_untrusted_forwarded_headers_are_ignored_and_invalid_client_ip_is_none(monkeypatch):
    """Trusting a spoofed forwarding chain would let callers forge audit fields."""
    monkeypatch.setattr("app.services.request_metadata.settings.trust_proxy_headers", False)
    request = _request(
        headers={
            "Host": "a.example",
            "X-Real-IP": "203.0.113.24",
            "X-Forwarded-For": "198.51.100.77",
            "X-Forwarded-Proto": "https",
            "X-Forwarded-Host": "spoofed.example",
        },
        client_ip="not-an-ip",
    )

    metadata = extract_request_metadata(request)

    assert metadata.ip is None
    assert metadata.request_url == "http://a.example/CampaignA?source=test"
