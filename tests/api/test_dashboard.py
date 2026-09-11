from datetime import datetime, timedelta, timezone

from sqlalchemy import event

from app.db import engine
from app.db.models import AccessLog
from tests.api.test_logs import _auth, _authorized_domain, _log


async def test_dashboard_aggregates_only_the_authorized_domain_without_n_plus_one_payloads(client, read_token):
    """A dashboard that aggregates before authorization can disclose another domain's traffic totals."""
    domain = await _authorized_domain()
    other_domain = await _authorized_domain(grant=False)
    now = datetime.now(timezone.utc)
    await _log(domain.id, accessed_at=now, short_code="top", note="top note", result="allowed", reason=None, ip="203.0.113.1", referer="https://a.example")
    await _log(domain.id, accessed_at=now - timedelta(hours=1), short_code="top", note="top note", result="blocked", reason="bot", ip="203.0.113.1", referer="https://a.example")
    await _log(domain.id, accessed_at=now - timedelta(days=1), short_code="second", note="second note", result="blocked", reason="country", ip="203.0.113.2", referer="https://b.example")
    await _log(domain.id, accessed_at=now - timedelta(days=1), short_code="empty", note=None, result="error", reason="other", ip=None, referer="   ")
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
    assert all(row["referer"].strip() for row in payload["top_referers"])

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


async def test_dashboard_groups_note_history_by_stable_link_and_uses_stable_ties(client, read_token):
    """Changing a link note must not create two leaderboard entries for the same short link."""
    domain = await _authorized_domain()
    now = datetime.now(timezone.utc)
    from tests.api.test_logs import _short_link

    alpha = await _short_link(domain.id, "alpha")
    beta = await _short_link(domain.id, "beta")
    await _log(domain.id, accessed_at=now, short_code="alpha", note="old note", short_link_id=alpha.id)
    await _log(domain.id, accessed_at=now - timedelta(seconds=1), short_code="alpha", note="new note", short_link_id=alpha.id)
    await _log(domain.id, accessed_at=now, short_code="beta", note="beta note", short_link_id=beta.id)
    await _log(domain.id, accessed_at=now - timedelta(seconds=1), short_code="beta", note="beta note", short_link_id=beta.id)

    response = await client.get(
        "/api/dashboard", headers=_auth(read_token), params={"domain_id": str(domain.id), "days": 7}
    )

    assert response.status_code == 200, response.text
    expected_by_identity = {
        str(alpha.id): {"short_code": "alpha", "short_link_note": "old note", "total": 2},
        str(beta.id): {"short_code": "beta", "short_link_note": "beta note", "total": 2},
    }
    expected = [expected_by_identity[identity] for identity in sorted(expected_by_identity)]
    assert response.json()["top_links"][:2] == expected


async def test_dashboard_applies_a_stable_top_link_cutoff_to_orphaned_snapshots(client, read_token):
    """Deleted links retain null IDs, so tied historical snapshots still need deterministic ranking."""
    domain = await _authorized_domain()
    now = datetime.now(timezone.utc)
    codes = ["kilo", "alpha", "delta", "bravo", "hotel", "echo"]
    for short_code in codes:
        await _log(
            domain.id,
            accessed_at=now,
            short_code=short_code,
            short_link_id=None,
            note=f"{short_code} snapshot",
        )

    response = await client.get(
        "/api/dashboard", headers=_auth(read_token), params={"domain_id": str(domain.id), "days": 7}
    )

    assert response.status_code == 200, response.text
    assert [row["short_code"] for row in response.json()["top_links"]] == sorted(codes)[:5]


async def test_dashboard_uses_a_constant_number_of_real_sql_statements_and_does_not_aggregate_denied_domains(
    client, read_token
):
    """Dashboard rows must be aggregate queries, not one query per ranking result or a denied-domain probe."""
    domain = await _authorized_domain()
    denied_domain = await _authorized_domain(grant=False)
    now = datetime.now(timezone.utc)
    for number in range(8):
        await _log(domain.id, accessed_at=now - timedelta(minutes=number), short_code=f"link-{number}")

    statements: list[str] = []

    def record(_, __, statement, ___, ____, _____):
        statements.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", record)
    try:
        response = await client.get(
            "/api/dashboard", headers=_auth(read_token), params={"domain_id": str(domain.id), "days": 7}
        )
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", record)

    assert response.status_code == 200, response.text
    assert len(statements) == 6

    denied_statements: list[str] = []

    def record_denied(_, __, statement, ___, ____, _____):
        denied_statements.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", record_denied)
    try:
        denied = await client.get(
            "/api/dashboard", headers=_auth(read_token), params={"domain_id": str(denied_domain.id), "days": 7}
        )
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", record_denied)

    assert denied.status_code == 403
    assert not any("access_logs" in statement.lower() for statement in denied_statements)
