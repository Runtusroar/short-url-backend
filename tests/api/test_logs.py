from datetime import datetime, timedelta, timezone
from uuid import uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.config import settings
from app.db import AsyncSessionLocal
from app.db.models import AccessLog, Domain, User, UserDomainAccess


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _authorized_domain(*, grant: bool = True) -> Domain:
    """Create committed data because API requests use an independent database session."""
    async with AsyncSessionLocal() as session:
        reader = await session.scalar(select(User).where(User.username == "reader"))
        assert reader is not None
        domain = Domain(name=f"{uuid4().hex}.logs.test")
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
) -> AccessLog:
    async with AsyncSessionLocal() as session:
        row = AccessLog(
            id=log_id or uuid4(),
            domain_id=domain_id,
            # Associations can legitimately be null after an audited link/target is removed.
            short_link_id=None,
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
