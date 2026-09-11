from datetime import datetime, timedelta, timezone

from tests.api.test_logs import _auth, _authorized_domain, _log


async def test_dashboard_aggregates_only_the_authorized_domain_without_n_plus_one_payloads(client, read_token):
    """A dashboard that aggregates before authorization can disclose another domain's traffic totals."""
    domain = await _authorized_domain()
    other_domain = await _authorized_domain(grant=False)
    now = datetime.now(timezone.utc)
    await _log(domain.id, accessed_at=now, short_code="top", note="top note", result="allowed", reason=None, ip="203.0.113.1", referer="https://a.example")
    await _log(domain.id, accessed_at=now - timedelta(hours=1), short_code="top", note="top note", result="blocked", reason="bot", ip="203.0.113.1", referer="https://a.example")
    await _log(domain.id, accessed_at=now - timedelta(days=1), short_code="second", note="second note", result="blocked", reason="country", ip="203.0.113.2", referer="https://b.example")
    await _log(domain.id, accessed_at=now - timedelta(days=1), short_code="empty", note=None, result="error", reason="other", ip=None, referer=None)
    await _log(other_domain.id, accessed_at=now, short_code="outside", result="allowed", reason=None, ip="203.0.113.9")

    response = await client.get(
        "/api/dashboard",
        headers=_auth(read_token),
        params={"domain_id": str(domain.id), "days": 7},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["total"] == 4
    assert payload["allowed"] == 1
    assert payload["blocked"] == 2
    assert payload["unique_ips"] == 2
    assert len(payload["daily"]) == 7
    assert sum(day["total"] for day in payload["daily"]) == 4
    assert payload["top_links"][0] == {"short_code": "top", "short_link_note": "top note", "total": 2}
    assert payload["top_referers"][0] == {"referer": "https://a.example", "total": 2}

    denied = await client.get(
        "/api/dashboard",
        headers=_auth(read_token),
        params={"domain_id": str(other_domain.id), "days": 7},
    )
    assert denied.status_code == 403


async def test_dashboard_accepts_only_its_bounded_seven_or_thirty_day_windows(client, read_token):
    """Allowing arbitrary dashboard windows can turn an overview query into an unbounded aggregation."""
    domain = await _authorized_domain()

    invalid = await client.get(
        "/api/dashboard",
        headers=_auth(read_token),
        params={"domain_id": str(domain.id), "days": 14},
    )

    assert invalid.status_code == 422
