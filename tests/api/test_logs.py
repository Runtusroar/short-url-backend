from datetime import datetime, timedelta, timezone
from uuid import uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.core.config import settings
from app.db import AsyncSessionLocal
from app.db.models import AccessLog, Domain, ShortLink, User, UserDomainAccess
from app.main import app


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _authorized_domain(*, grant: bool = True, is_active: bool = True) -> Domain:
    """Create committed data because API requests use an independent database session."""
    async with AsyncSessionLocal() as session:
        reader = await session.scalar(select(User).where(User.username == "reader"))
        assert reader is not None
        domain = Domain(name=f"{uuid4().hex}.logs.test", is_active=is_active)
        session.add(domain)
        await session.flush()
        if grant:
            session.add(UserDomainAccess(user_id=reader.id, domain_id=domain.id, access_level="read"))
        await session.commit()
        return domain


async def _log(
    domain_id,
    *,
    accessed_at: datetime,
    short_code: str = "summer-sale",
    note: str | None = "campaign note",
    country: str | None = "CN",
    result: str = "blocked",
    reason: str | None = "country",
    ip: str | None = "203.0.113.1",
    referer: str | None = "https://partner.example/source",
    log_id=None,
    short_link_id=None,
) -> AccessLog:
    async with AsyncSessionLocal() as session:
        row = AccessLog(
            id=log_id or uuid4(),
            domain_id=domain_id,
            # Associations can legitimately be null after an audited link/target is removed.
            short_link_id=short_link_id,
            target_url_id=None,
            request_url=f"https://{short_code}.example/{short_code}",
            domain_name="snapshot.example",
            short_code=short_code,
            short_link_note=note,
            target_url="https://destination.example/landing",
            result=result,
            block_reason=reason,
            block_detail="blocked by the fixture policy",
            ip=ip,
            country_code=country,
            referer=referer,
            ua_raw="Mozilla/5.0 fixture",
            ua_browser="Firefox",
            ua_browser_version="120",
            ua_os="Linux",
            ua_os_version="6",
            ua_device_type="desktop",
            ua_device_brand="Generic",
            ua_device_model="Fixture",
            ua_bot_name=None,
            accessed_at=accessed_at,
        )
        session.add(row)
        await session.commit()
        return row


async def _short_link(domain_id, short_code: str) -> ShortLink:
    async with AsyncSessionLocal() as session:
        link = ShortLink(domain_id=domain_id, short_code=short_code, is_active=True)
        session.add(link)
        await session.commit()
        return link


async def test_access_logs_scope_before_filters_and_return_complete_snapshots(client, read_token):
    """Filtering before domain authorization would let a subaccount investigate another domain's logs."""
    domain = await _authorized_domain()
    now = datetime.now(timezone.utc)
    wanted = await _log(domain.id, accessed_at=now, short_code="needle-code", note="irrelevant")
    note_match = await _log(
        domain.id, accessed_at=now - timedelta(seconds=1), short_code="other", note="needle note"
    )
    await _log(domain.id, accessed_at=now - timedelta(seconds=2), country="US")
    other_domain = await _authorized_domain(grant=False)
    await _log(other_domain.id, accessed_at=now, short_code="needle-code", note="outside grant")

    response = await client.get(
        "/api/access-logs",
        headers=_auth(read_token),
        params={
            "domain_id": str(domain.id),
            "keyword": "needle",
            "country": "CN",
            "result": "blocked",
            "reason": "country",
        },
    )

    assert response.status_code == 200, response.text
    rows = response.json()["items"]
    # The note snapshot is intentionally matched without needing a live short-link join.
    assert {row["id"] for row in rows} == {str(wanted.id), str(note_match.id)}
    assert len(rows) == 2
    row = next(row for row in rows if row["id"] == str(wanted.id))
    assert row["request_url"] == "https://needle-code.example/needle-code"
    assert row["short_code"] == "needle-code"
    assert row["short_link_note"] == "irrelevant"
    assert row["country_code"] == "CN"
    assert row["referer"] == "https://partner.example/source"
    assert row["result"] == "blocked"
    assert row["block_reason"] == "country"
    assert row["block_detail"] == "blocked by the fixture policy"
    assert row["ua_browser"] == "Firefox"
    assert row["ua_device_model"] == "Fixture"

    denied = await client.get(
        "/api/access-logs",
        headers=_auth(read_token),
        params={"domain_id": str(other_domain.id)},
    )
    assert denied.status_code == 403


async def test_subaccount_domain_failures_use_one_denial_envelope_for_logs_and_dashboard(client, read_token):
    """Different responses for missing, inactive, and ungranted domains reveal domain inventory to subaccounts."""
    ungranted = await _authorized_domain(grant=False)
    inactive = await _authorized_domain(is_active=False)
    missing = uuid4()

    for endpoint, extra_params in (("/api/access-logs", {}), ("/api/dashboard", {"days": 7})):
        for domain_id in (ungranted.id, inactive.id, missing):
            response = await client.get(
                endpoint,
                headers=_auth(read_token),
                params={"domain_id": str(domain_id), **extra_params},
            )
            assert response.status_code == 403
            assert response.json()["code"] == "PERMISSION_DENIED"


async def test_access_logs_use_timezone_aware_inclusive_time_boundaries(client, read_token):
    """Using UTC calendar dates for local filtering would drop visits near midnight in the configured zone."""
    domain = await _authorized_domain()
    local = ZoneInfo(settings.app_timezone)
    start = datetime(2026, 9, 12, 0, 0, tzinfo=local)
    end = datetime(2026, 9, 12, 23, 59, 59, tzinfo=local)
    at_start = await _log(domain.id, accessed_at=start.astimezone(timezone.utc))
    at_end = await _log(domain.id, accessed_at=end.astimezone(timezone.utc))
    await _log(domain.id, accessed_at=(start - timedelta(microseconds=1)).astimezone(timezone.utc))
    await _log(domain.id, accessed_at=(end + timedelta(microseconds=1)).astimezone(timezone.utc))

    response = await client.get(
        "/api/access-logs",
        headers=_auth(read_token),
        params={
            "domain_id": str(domain.id),
            "date_from": start.isoformat(),
            "date_to": end.isoformat(),
        },
    )

    assert response.status_code == 200, response.text
    assert {row["id"] for row in response.json()["items"]} == {str(at_start.id), str(at_end.id)}


async def test_access_logs_cursor_is_stable_for_equal_timestamps_and_rejects_tampering(client, read_token):
    """Ordering only by timestamp can duplicate or skip records that were logged in the same instant."""
    domain = await _authorized_domain()
    instant = datetime(2026, 9, 12, 12, tzinfo=timezone.utc)
    rows = [await _log(domain.id, accessed_at=instant, log_id=uuid4()) for _ in range(5)]

    first = await client.get(
        "/api/access-logs",
        headers=_auth(read_token),
        params={"domain_id": str(domain.id), "page_size": 2, "result": "blocked"},
    )
    assert first.status_code == 200, first.text
    second = await client.get(
        "/api/access-logs",
        headers=_auth(read_token),
        params={
            "domain_id": str(domain.id),
            "page_size": 2,
            "result": "blocked",
            "cursor": first.json()["next_cursor"],
        },
    )
    third = await client.get(
        "/api/access-logs",
        headers=_auth(read_token),
        params={
            "domain_id": str(domain.id),
            "page_size": 2,
            "result": "blocked",
            "cursor": second.json()["next_cursor"],
        },
    )

    assert second.status_code == 200, second.text
    assert third.status_code == 200, third.text
    returned = [*first.json()["items"], *second.json()["items"], *third.json()["items"]]
    assert [row["id"] for row in returned] == [str(row.id) for row in sorted(rows, key=lambda row: row.id, reverse=True)]
    assert len({row["id"] for row in returned}) == len(rows)
    assert third.json()["next_cursor"] is None

    malformed = await client.get(
        "/api/access-logs",
        headers=_auth(read_token),
        params={"domain_id": str(domain.id), "cursor": "forged.cursor"},
    )
    assert malformed.status_code == 422
    assert malformed.json()["code"] == "INVALID_CURSOR"


async def test_access_logs_reject_out_of_range_page_sizes_and_non_ascii_country_codes(client, read_token):
    """Unbounded pages and non-ISO-like country input make the investigation API unpredictable."""
    domain = await _authorized_domain()

    for params in (
        {"domain_id": str(domain.id), "page_size": 0},
        {"domain_id": str(domain.id), "page_size": 101},
        {"domain_id": str(domain.id), "country": "中国"},
    ):
        response = await client.get("/api/access-logs", headers=_auth(read_token), params=params)
        assert response.status_code == 422


async def test_access_logs_treat_like_metacharacters_as_literal_keyword_text(client, read_token):
    """An unescaped percent, underscore, or slash must not turn an audit search into a wildcard query."""
    domain = await _authorized_domain()
    now = datetime.now(timezone.utc)
    literal = await _log(domain.id, accessed_at=now, short_code="sale%_\\code")
    await _log(domain.id, accessed_at=now - timedelta(seconds=1), short_code="saleXXcode")

    response = await client.get(
        "/api/access-logs",
        headers=_auth(read_token),
        params={"domain_id": str(domain.id), "keyword": "%_\\"},
    )

    assert response.status_code == 200, response.text
    assert [row["id"] for row in response.json()["items"]] == [str(literal.id)]


async def test_access_logs_reject_dst_gaps_folds_and_reversed_local_ranges(client, read_token, monkeypatch):
    """Naive wall times in a DST gap/fold are not a single instant and must require an explicit offset."""
    monkeypatch.setattr(settings, "app_timezone", "America/New_York")
    domain = await _authorized_domain()

    for params in (
        {"date_from": "2026-03-08T02:30:00"},
        {"date_from": "2026-11-01T01:30:00"},
        {"date_from": "2026-09-12T17:00:00", "date_to": "2026-09-12T16:00:00"},
    ):
        response = await client.get(
            "/api/access-logs",
            headers=_auth(read_token),
            params={"domain_id": str(domain.id), **params},
        )
        assert response.status_code == 422

    explicit_offset = await client.get(
        "/api/access-logs",
        headers=_auth(read_token),
        params={
            "domain_id": str(domain.id),
            "date_from": "2026-11-01T01:30:00-04:00",
            "date_to": "2026-11-01T01:30:00-05:00",
        },
    )
    assert explicit_offset.status_code == 200


def test_access_log_openapi_exposes_the_closed_result_and_reason_enums():
    """Clients need the same finite filter/output values the persisted API contract emits."""
    schemas = app.openapi()["components"]["schemas"]

    assert schemas["AccessResult"]["enum"] == ["allowed", "blocked", "error"]
    assert schemas["BlockReason"]["enum"] == [
        "ip",
        "proxy",
        "country",
        "bot",
        "platform",
        "referer",
        "other",
    ]
    response_properties = schemas["AccessLogResponse"]["properties"]
    assert response_properties["result"] == {"$ref": "#/components/schemas/AccessResult"}
    assert response_properties["block_reason"]["anyOf"] == [
        {"$ref": "#/components/schemas/BlockReason"},
        {"type": "null"},
    ]
