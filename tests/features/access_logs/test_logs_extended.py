"""Extended access log query tests."""

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import settings
from app.features.access_logs.cursor import decode_cursor
from app.features.access_logs.query import AccessLogFilters
from app.features.access_logs.service import list_logs as list_logs_query
from app.models import AccessLog, Domain, ShortLink, User
from tests.conftest import _sync_engine


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _host(domain: str = "test.local") -> dict:
    return {"Host": domain}


async def _create_link_and_log(client: AsyncClient, token: str, domain_id: str) -> dict:
    resp = await client.post(
        "/api/short-links",
        headers={**_auth(token), **_host()},
        json={"domain_id": domain_id, "name": "log test"},
    )
    assert resp.status_code == 200
    link = resp.json()

    resp = await client.post(
        f"/api/short-links/{link['id']}/urls",
        headers={**_auth(token), **_host()},
        json={"url": "https://log.example.com", "url_type": "allowed", "weight": 1, "is_active": True},
    )
    assert resp.status_code == 200

    for _ in range(2):
        await client.get(
            f"/{link['short_code']}",
            headers={**_host(), "X-Forwarded-For": "10.0.0.10"},
            follow_redirects=False,
        )

    return link


async def _create_named_link(
    client: AsyncClient,
    token: str,
    domain_id: str,
    *,
    alias: str,
    name: str,
) -> dict:
    response = await client.post(
        "/api/short-links",
        headers={**_auth(token), **_host()},
        json={"domain_id": domain_id, "custom_alias": alias, "name": name},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _insert_access_log(
    *,
    log_id: UUID,
    short_link_id: str,
    domain_id: str,
    accessed_at: datetime,
    access_date: date,
    country: str | None,
    result: str,
) -> None:
    with _sync_engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO access_logs (
                    id, short_link_id, domain_id, result, client_ip, country,
                    accessed_at, access_date, dedup_bucket, decision_reason,
                    request_method, proxy_check_status, proxy_types
                ) VALUES (
                    :id, :short_link_id, :domain_id, :result, '203.0.113.9',
                    :country, :accessed_at, :access_date, :dedup_bucket,
                    'legacy_unknown', 'GET', 'skipped', '[]'::jsonb
                )
                """
            ),
            {
                "id": str(log_id),
                "short_link_id": short_link_id,
                "domain_id": domain_id,
                "result": result,
                "country": country,
                "accessed_at": accessed_at,
                "access_date": access_date,
                "dedup_bucket": log_id.int % 2_000_000_000,
            },
        )


@dataclass(frozen=True)
class SeededLogSearchRecords:
    august_id: UUID
    promo_backup_id: UUID
    blocked_backup_id: UUID
    us_campaign_id: UUID
    august_link_id: str
    promo_backup_link_id: str
    us_campaign_link_id: str
    august_name: str
    promo_backup_name: str
    us_campaign_name: str
    domain_id: str
    domain_name: str


@pytest.fixture
async def seeded_log_search_records(
    client: AsyncClient, admin_token: str, default_domain: dict
) -> SeededLogSearchRecords:
    suffix = uuid4().hex[:8]
    domain_response = await client.post(
        "/api/domains",
        headers=_auth(admin_token),
        json={"name": f"search-{suffix}.test"},
    )
    assert domain_response.status_code == 200, domain_response.text
    search_domain = domain_response.json()
    august = await _create_named_link(
        client,
        admin_token,
        search_domain["id"],
        alias=f"promo-aug-{suffix}",
        name="August Promotion",
    )
    backup = await _create_named_link(
        client,
        admin_token,
        search_domain["id"],
        alias=f"promo-backup-{suffix}",
        name="Promo Backup",
    )
    us_campaign = await _create_named_link(
        client,
        admin_token,
        search_domain["id"],
        alias=f"us-campaign-{suffix}",
        name="US Campaign",
    )
    ids = (uuid4(), uuid4(), uuid4(), uuid4())
    rows = (
        (ids[0], august["id"], datetime(2026, 8, 16, 12, tzinfo=timezone.utc), date(2026, 8, 16), "CN", "allowed"),
        (ids[1], backup["id"], datetime(2026, 8, 15, 12, tzinfo=timezone.utc), date(2026, 8, 15), None, "denied"),
        (ids[2], backup["id"], datetime(2026, 8, 15, 11, tzinfo=timezone.utc), date(2026, 8, 15), None, "blocked"),
        (ids[3], us_campaign["id"], datetime(2026, 8, 14, 12, tzinfo=timezone.utc), date(2026, 8, 14), "US", "allowed"),
    )
    for log_id, link_id, accessed_at, access_date, country, result in rows:
        _insert_access_log(
            log_id=log_id,
            short_link_id=link_id,
            domain_id=search_domain["id"],
            accessed_at=accessed_at,
            access_date=access_date,
            country=country,
            result=result,
        )
    return SeededLogSearchRecords(
        august_id=ids[0],
        promo_backup_id=ids[1],
        blocked_backup_id=ids[2],
        us_campaign_id=ids[3],
        august_link_id=august["id"],
        promo_backup_link_id=backup["id"],
        us_campaign_link_id=us_campaign["id"],
        august_name="August Promotion",
        promo_backup_name="Promo Backup",
        us_campaign_name="US Campaign",
        domain_id=search_domain["id"],
        domain_name=search_domain["name"],
    )


async def test_operator_can_view_own_link_logs(
    client: AsyncClient, operator_token: str, default_domain: dict
):
    link = await _create_link_and_log(client, operator_token, default_domain["id"])

    resp = await client.get(
        "/api/logs",
        headers={**_auth(operator_token), **_host()},
        params={"short_link_id": link["id"]},
    )
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["has_more"] is False
    assert payload["next_cursor"] is None
    assert len(payload["items"]) == 2


async def test_soft_deleted_short_link_keeps_authorized_historical_logs(
    client: AsyncClient, operator_token: str, default_domain: dict
):
    link = await _create_link_and_log(client, operator_token, default_domain["id"])

    deleted = await client.delete(
        f"/api/short-links/{link['id']}",
        headers={**_auth(operator_token), **_host()},
    )
    assert deleted.status_code == 200

    redirect = await client.get(
        f"/{link['short_code']}", headers=_host(), follow_redirects=False
    )
    assert redirect.status_code == 404

    logs = await client.get(
        "/api/logs",
        headers={**_auth(operator_token), **_host()},
        params={"short_link_id": link["id"]},
    )
    assert logs.status_code == 200
    assert len(logs.json()["items"]) == 2


async def test_operator_cannot_view_other_link_logs(
    client: AsyncClient, admin_token: str, operator_token: str, default_domain: dict
):
    link = await _create_link_and_log(client, admin_token, default_domain["id"])

    resp = await client.get(
        "/api/logs",
        headers={**_auth(operator_token), **_host()},
        params={"short_link_id": link["id"]},
    )
    assert resp.status_code == 403


async def test_client_can_view_granted_link_logs(
    client: AsyncClient, admin_token: str, client_token: str, default_domain: dict
):
    link = await _create_link_and_log(client, admin_token, default_domain["id"])

    me = await client.get("/api/auth/me", headers=_auth(client_token))
    client_id = me.json()["id"]

    await client.post(
        f"/api/short-links/{link['id']}/permissions",
        headers={**_auth(admin_token), **_host()},
        json={"user_id": client_id},
    )

    resp = await client.get(
        "/api/logs",
        headers={**_auth(client_token), **_host()},
        params={"short_link_id": link["id"]},
    )
    assert resp.status_code == 200
    assert len(resp.json()["items"]) == 2


async def test_logs_date_filter(
    client: AsyncClient, admin_token: str, default_domain: dict
):
    link = await _create_link_and_log(client, admin_token, default_domain["id"])

    today = date.today()
    yesterday = today - timedelta(days=1)

    resp = await client.get(
        "/api/logs",
        headers={**_auth(admin_token), **_host()},
        params={
            "short_link_id": link["id"],
            "date_from": str(today),
            "date_to": str(today),
        },
    )
    assert resp.status_code == 200
    assert len(resp.json()["items"]) == 2

    resp = await client.get(
        "/api/logs",
        headers={**_auth(admin_token), **_host()},
        params={
            "short_link_id": link["id"],
            "date_from": str(yesterday),
            "date_to": str(yesterday),
        },
    )
    assert resp.status_code == 200
    assert len(resp.json()["items"]) == 0


async def test_logs_pagination(
    client: AsyncClient, admin_token: str, default_domain: dict
):
    link = await _create_link_and_log(client, admin_token, default_domain["id"])

    resp = await client.get(
        "/api/logs",
        headers={**_auth(admin_token), **_host()},
        params={"short_link_id": link["id"], "limit": 1},
    )
    assert resp.status_code == 200
    first_page = resp.json()
    assert len(first_page["items"]) == 1
    assert first_page["has_more"] is True
    assert first_page["next_cursor"]

    resp = await client.get(
        "/api/logs",
        headers={**_auth(admin_token), **_host()},
        params={
            "short_link_id": link["id"],
            "limit": 1,
            "cursor": first_page["next_cursor"],
            "offset": 10,
        },
    )
    assert resp.status_code == 200
    assert len(resp.json()["items"]) == 1


@pytest.mark.parametrize("cursor", ("", "x" * 2049))
async def test_invalid_cursor_never_restarts_from_the_first_page(
    client: AsyncClient,
    admin_token: str,
    default_domain: dict,
    cursor: str,
):
    link = await _create_link_and_log(client, admin_token, default_domain["id"])

    first_page = await client.get(
        "/api/logs",
        headers={**_auth(admin_token), **_host()},
        params={"short_link_id": link["id"], "limit": 1},
    )
    assert first_page.status_code == 200
    assert first_page.json()["has_more"] is True

    response = await client.get(
        "/api/logs",
        headers={**_auth(admin_token), **_host()},
        params={"short_link_id": link["id"], "limit": 1, "cursor": cursor},
    )

    assert response.status_code == 400
    assert response.json() == {"code": "INVALID_CURSOR", "message": "无效的分页游标"}


async def test_logs_preserve_rule_and_target_snapshots_after_management_changes(
    client: AsyncClient, admin_token: str, default_domain: dict
):
    link = await client.post(
        "/api/short-links",
        headers={**_auth(admin_token), **_host()},
        json={"domain_id": default_domain["id"], "name": "snapshot test"},
    )
    assert link.status_code == 200
    link = link.json()
    target = await client.post(
        f"/api/short-links/{link['id']}/urls",
        headers={**_auth(admin_token), **_host()},
        json={"url": "https://before.example", "url_type": "allowed", "weight": 1},
    )
    assert target.status_code == 200
    target = target.json()
    rule = await client.post(
        f"/api/short-links/{link['id']}/rules",
        headers={**_auth(admin_token), **_host()},
        json={"name": "allow snapshot", "action": "allow", "priority": 0},
    )
    assert rule.status_code == 200
    rule = rule.json()

    for method in ("get", "head"):
        response = await getattr(client, method)(
            f"/{link['short_code']}",
            headers={**_host("TEST.LOCAL:80"), "User-Agent": "Mozilla/5.0"},
            follow_redirects=False,
        )
        assert response.status_code == 302

    updated = await client.put(
        f"/api/short-links/{link['id']}/urls/{target['id']}",
        headers={**_auth(admin_token), **_host()},
        json={"url": "https://after.example"},
    )
    assert updated.status_code == 200
    assert (
        await client.delete(
            f"/api/short-links/{link['id']}/rules/{rule['id']}",
            headers={**_auth(admin_token), **_host()},
        )
    ).status_code == 200
    assert (
        await client.delete(
            f"/api/short-links/{link['id']}/urls/{target['id']}",
            headers={**_auth(admin_token), **_host()},
        )
    ).status_code == 200

    logs = await client.get(
        "/api/logs",
        headers={**_auth(admin_token), **_host()},
        params={"short_link_id": link["id"]},
    )
    assert logs.status_code == 200
    assert {row["request_method"] for row in logs.json()["items"]} == {"GET", "HEAD"}
    for row in logs.json()["items"]:
        assert row["request_host"] == "test.local"
        assert row["decision_reason"] == "matched_rule"
        assert row["matched_rule_id"] is None
        assert row["matched_rule_name"] == "allow snapshot"
        assert row["target_url_id"] is None
        assert row["target_url_snapshot"] == "https://before.example"


async def test_logs_record_explicit_default_and_blacklist_reasons(
    client: AsyncClient, admin_token: str, default_domain: dict, monkeypatch
):
    link = await client.post(
        "/api/short-links",
        headers={**_auth(admin_token), **_host()},
        json={"domain_id": default_domain["id"], "name": "decision reason test"},
    )
    assert link.status_code == 200
    link = link.json()
    denied_target = await client.post(
        f"/api/short-links/{link['id']}/urls",
        headers={**_auth(admin_token), **_host()},
        json={"url": "https://denied.example", "url_type": "denied", "weight": 1},
    )
    assert denied_target.status_code == 200
    default_denied = await client.get(
        f"/{link['short_code']}", headers=_host(), follow_redirects=False
    )
    assert default_denied.status_code == 302

    monkeypatch.setattr(settings, "trust_proxy_headers", True)
    assert (
        await client.post(
            "/api/ip-blacklist",
            headers=_auth(admin_token),
            json={"ip": "203.0.113.77", "reason": "reason test"},
        )
    ).status_code == 200
    blacklisted = await client.get(
        f"/{link['short_code']}",
        headers={**_host(), "X-Real-IP": "203.0.113.77"},
        follow_redirects=False,
    )
    assert blacklisted.status_code == 302

    logs = await client.get(
        "/api/logs",
        headers={**_auth(admin_token), **_host()},
        params={"short_link_id": link["id"]},
    )
    assert logs.status_code == 200
    assert {row["decision_reason"] for row in logs.json()["items"]} == {
        "default_action",
        "blacklist",
    }


@pytest.mark.parametrize(
    ("params", "expected_names"),
    [
        ({"short_code": "promo"}, {"August Promotion", "Promo Backup"}),
        ({"name": "AUGUST"}, {"August Promotion"}),
        (
            {"country": ["CN", "US"]},
            {"August Promotion", "US Campaign"},
        ),
        ({"result": ["denied", "blocked"]}, {"Promo Backup"}),
        (
            {"date_from": "2026-08-16", "date_to": "2026-08-16"},
            {"August Promotion"},
        ),
        (
            {
                "short_code": "promo",
                "country": ["CN"],
                "result": ["allowed"],
            },
            {"August Promotion"},
        ),
    ],
)
async def test_log_filters_combine_with_and_and_multivalue_or(
    client,
    admin_token,
    seeded_log_search_records,
    params,
    expected_names,
):
    query_params = []
    for key, value in params.items():
        if isinstance(value, list):
            query_params.extend((key, item) for item in value)
        else:
            query_params.append((key, value))
    query_params.append(("domain_id", seeded_log_search_records.domain_id))
    response = await client.get(
        "/api/logs",
        params=query_params,
        headers={"Authorization": f"Bearer {admin_token}", "Host": "test.local"},
    )
    assert response.status_code == 200, response.text
    assert {
        row["short_link_name"] for row in response.json()["items"]
    } == expected_names


async def test_short_code_filter_normalizes_uppercase_and_rejects_sql_wildcards(
    client, admin_token, seeded_log_search_records
):
    uppercase = await client.get(
        "/api/logs",
        params={
            "short_code": "PROMO",
            "domain_id": seeded_log_search_records.domain_id,
        },
        headers={**_auth(admin_token), **_host()},
    )
    assert uppercase.status_code == 200
    assert {row["short_link_name"] for row in uppercase.json()["items"]} == {
        "August Promotion",
        "Promo Backup",
    }

    wildcard = await client.get(
        "/api/logs",
        params={
            "short_code": "promo%",
            "domain_id": seeded_log_search_records.domain_id,
        },
        headers={**_auth(admin_token), **_host()},
    )
    assert wildcard.status_code == 400
    assert wildcard.json()["code"] == "VALIDATION_ERROR"


async def test_short_code_filter_treats_legal_underscore_as_a_literal_prefix(
    client, admin_token, default_domain
):
    suffix = uuid4().hex[:8]
    underscore_link = await _create_named_link(
        client,
        admin_token,
        default_domain["id"],
        alias=f"promo_{suffix}",
        name="Underscore Prefix Campaign",
    )
    nonmatching_link = await _create_named_link(
        client,
        admin_token,
        default_domain["id"],
        alias=f"promoa{suffix}",
        name="Letter Prefix Campaign",
    )
    for log_id, link_id, hour in (
        (uuid4(), underscore_link["id"], 12),
        (uuid4(), nonmatching_link["id"], 11),
    ):
        _insert_access_log(
            log_id=log_id,
            short_link_id=link_id,
            domain_id=default_domain["id"],
            accessed_at=datetime(2026, 8, 16, hour, tzinfo=timezone.utc),
            access_date=date(2026, 8, 16),
            country="CN",
            result="allowed",
        )

    with _sync_engine.connect() as connection:
        unescaped_codes = set(
            connection.scalars(
                select(ShortLink.short_code).where(
                    ShortLink.id.in_(
                        (UUID(underscore_link["id"]), UUID(nonmatching_link["id"]))
                    ),
                    ShortLink.short_code.startswith("promo_", autoescape=False),
                )
            )
        )
    assert unescaped_codes == {
        underscore_link["short_code"],
        nonmatching_link["short_code"],
    }

    response = await client.get(
        "/api/logs",
        params={"short_code": "promo_"},
        headers={**_auth(admin_token), **_host()},
    )

    assert response.status_code == 200, response.text
    assert {row["short_link_name"] for row in response.json()["items"]} == {
        "Underscore Prefix Campaign"
    }


@pytest.mark.parametrize(
    "params",
    [
        {"date_from": "2026-08-17", "date_to": "2026-08-16"},
        {"country": "ZZ"},
        {"name": "   "},
        {"short_code": "%"},
    ],
)
async def test_log_filter_validation_uses_api_error_envelope(
    client, admin_token, params
):
    response = await client.get(
        "/api/logs", params=params, headers={**_auth(admin_token), **_host()}
    )
    assert response.status_code == 400
    assert response.json()["code"] == "VALIDATION_ERROR"


@dataclass(frozen=True)
class AuthorizationRecords:
    admin_link_id: str
    operator_link_id: str
    client_id: str


@pytest.fixture
async def authorization_records(
    client, admin_token, operator_token, client_token, default_domain
) -> AuthorizationRecords:
    suffix = uuid4().hex[:8]
    admin_link = await _create_named_link(
        client,
        admin_token,
        default_domain["id"],
        alias=f"admin-secret-{suffix}",
        name="Admin Secret Campaign",
    )
    operator_link = await _create_named_link(
        client,
        operator_token,
        default_domain["id"],
        alias=f"operator-owned-{suffix}",
        name="Operator Owned Campaign",
    )
    client_id = (await client.get("/api/auth/me", headers=_auth(client_token))).json()[
        "id"
    ]
    grant = await client.post(
        f"/api/short-links/{admin_link['id']}/permissions",
        headers={**_auth(admin_token), **_host()},
        json={"user_id": client_id},
    )
    assert grant.status_code == 200, grant.text
    for log_id, link_id, hour, country, result in (
        (uuid4(), admin_link["id"], 10, "CN", "allowed"),
        (uuid4(), operator_link["id"], 9, "US", "denied"),
    ):
        _insert_access_log(
            log_id=log_id,
            short_link_id=link_id,
            domain_id=default_domain["id"],
            accessed_at=datetime(2026, 8, 16, hour, tzinfo=timezone.utc),
            access_date=date(2026, 8, 16),
            country=country,
            result=result,
        )
    return AuthorizationRecords(admin_link["id"], operator_link["id"], client_id)


async def test_broad_log_query_is_scoped_by_current_role(
    client,
    admin_token,
    operator_token,
    client_token,
    authorization_records,
):
    expected = (
        (admin_token, {"Admin Secret Campaign", "Operator Owned Campaign"}),
        (operator_token, {"Operator Owned Campaign"}),
        (client_token, {"Admin Secret Campaign"}),
    )
    for token, expected_names in expected:
        response = await client.get(
            "/api/logs", headers={**_auth(token), **_host()}
        )
        assert response.status_code == 200
        names = {row["short_link_name"] for row in response.json()["items"]}
        assert names.intersection(
            {"Admin Secret Campaign", "Operator Owned Campaign"}
        ) == expected_names


async def test_explicit_inaccessible_links_are_forbidden_but_broad_filters_are_empty(
    client,
    operator_token,
    client_token,
    authorization_records,
):
    operator_explicit = await client.get(
        "/api/logs",
        params={"short_link_id": authorization_records.admin_link_id},
        headers={**_auth(operator_token), **_host()},
    )
    assert operator_explicit.status_code == 403
    client_explicit = await client.get(
        "/api/logs",
        params={"short_link_id": authorization_records.operator_link_id},
        headers={**_auth(client_token), **_host()},
    )
    assert client_explicit.status_code == 403

    for token, params in (
        (operator_token, {"name": "Admin Secret"}),
        (client_token, {"short_code": "operator-owned"}),
    ):
        response = await client.get(
            "/api/logs", params=params, headers={**_auth(token), **_host()}
        )
        assert response.status_code == 200
        assert response.json()["items"] == []


async def test_soft_deleted_granted_link_remains_in_client_broad_history(
    client, admin_token, client_token, authorization_records
):
    deleted = await client.delete(
        f"/api/short-links/{authorization_records.admin_link_id}",
        headers={**_auth(admin_token), **_host()},
    )
    assert deleted.status_code == 200
    response = await client.get(
        "/api/logs", headers={**_auth(client_token), **_host()}
    )
    assert response.status_code == 200
    assert "Admin Secret Campaign" in {
        row["short_link_name"] for row in response.json()["items"]
    }


async def test_non_admin_cannot_override_effective_domain(
    client, admin_token, operator_token, authorization_records
):
    domain = await client.post(
        "/api/domains",
        headers=_auth(admin_token),
        json={"name": f"other-{uuid4().hex[:8]}.test"},
    )
    assert domain.status_code == 200
    response = await client.get(
        "/api/logs",
        params={"domain_id": domain.json()["id"]},
        headers={**_auth(operator_token), **_host()},
    )
    assert response.status_code == 403


async def test_explicit_link_authorization_intersects_effective_domain(
    client, admin_token, default_domain
):
    suffix = uuid4().hex[:8]
    domain_b_response = await client.post(
        "/api/domains",
        headers=_auth(admin_token),
        json={"name": f"explicit-scope-{suffix}.test"},
    )
    assert domain_b_response.status_code == 200, domain_b_response.text
    domain_b = domain_b_response.json()

    users = {}
    for role in ("operator", "client"):
        username = f"scope-{role[:2]}-{suffix}"
        created = await client.post(
            "/api/admin/users",
            headers=_auth(admin_token),
            json={
                "username": username,
                "password": "secret123",
                "role": role,
                "domain_ids": [default_domain["id"], domain_b["id"]],
            },
        )
        assert created.status_code == 200, created.text
        login = await client.post(
            "/api/auth/login",
            data={"username": username, "password": "secret123"},
        )
        assert login.status_code == 200, login.text
        users[role] = {
            "id": created.json()["id"],
            "token": login.json()["access_token"],
        }

    operator_link_response = await client.post(
        "/api/short-links",
        headers={**_auth(users["operator"]["token"]), **_host(domain_b["name"])},
        json={
            "domain_id": domain_b["id"],
            "custom_alias": f"scope-op-{suffix}",
            "name": "Cross-domain operator link",
        },
    )
    assert operator_link_response.status_code == 200, operator_link_response.text
    operator_link = operator_link_response.json()

    client_link_response = await client.post(
        "/api/short-links",
        headers={**_auth(admin_token), **_host(domain_b["name"])},
        json={
            "domain_id": domain_b["id"],
            "custom_alias": f"scope-client-{suffix}",
            "name": "Cross-domain client link",
        },
    )
    assert client_link_response.status_code == 200, client_link_response.text
    client_link = client_link_response.json()
    granted = await client.post(
        f"/api/short-links/{client_link['id']}/permissions",
        headers={**_auth(admin_token), **_host(domain_b["name"])},
        json={"user_id": users["client"]["id"]},
    )
    assert granted.status_code == 200, granted.text

    status_by_role = {}
    for role, token, link_id in (
        ("admin", admin_token, client_link["id"]),
        ("operator", users["operator"]["token"], operator_link["id"]),
        ("client", users["client"]["token"], client_link["id"]),
    ):
        response = await client.get(
            "/api/logs",
            headers={**_auth(token), **_host()},
            params={"short_link_id": link_id},
        )
        status_by_role[role] = response.status_code

    assert status_by_role == {"admin": 403, "operator": 403, "client": 403}


async def test_query_requires_both_log_and_short_link_to_match_effective_domain(
    client, admin_token, default_domain
):
    other_domain = await client.post(
        "/api/domains",
        headers=_auth(admin_token),
        json={"name": f"boundary-{uuid4().hex[:8]}.test"},
    )
    assert other_domain.status_code == 200
    other = other_domain.json()
    local_link = await _create_named_link(
        client,
        admin_token,
        default_domain["id"],
        alias=f"local-boundary-{uuid4().hex[:8]}",
        name="Local Boundary",
    )
    foreign_response = await client.post(
        "/api/short-links",
        headers={**_auth(admin_token), **_host(other["name"])},
        json={
            "domain_id": other["id"],
            "custom_alias": f"foreign-boundary-{uuid4().hex[:8]}",
            "name": "Foreign Boundary",
        },
    )
    assert foreign_response.status_code == 200, foreign_response.text
    foreign_link = foreign_response.json()
    for log_id, link_id, domain_id, hour in (
        (uuid4(), foreign_link["id"], default_domain["id"], 8),
        (uuid4(), local_link["id"], other["id"], 7),
    ):
        _insert_access_log(
            log_id=log_id,
            short_link_id=link_id,
            domain_id=domain_id,
            accessed_at=datetime(2026, 8, 16, hour, tzinfo=timezone.utc),
            access_date=date(2026, 8, 16),
            country="CN",
            result="allowed",
        )
    response = await client.get(
        "/api/logs", headers={**_auth(admin_token), **_host()}
    )
    assert response.status_code == 200
    names = {row["short_link_name"] for row in response.json()["items"]}
    assert "Local Boundary" not in names
    assert "Foreign Boundary" not in names


@dataclass(frozen=True)
class KeysetRecords:
    link_id: str
    name: str
    descending_ids: tuple[str, ...]
    accessed_at: datetime
    domain_id: str


@pytest.fixture
async def keyset_records(client, admin_token, default_domain):
    name = f"Stable Page {uuid4().hex[:8]}"
    link = await _create_named_link(
        client,
        admin_token,
        default_domain["id"],
        alias=f"stable-page-{uuid4().hex[:8]}",
        name=name,
    )
    accessed_at = datetime(2026, 8, 16, 6, tzinfo=timezone.utc)
    ascending = tuple(
        UUID(f"40000000-0000-0000-0000-{number:012d}") for number in range(1, 6)
    )
    for index, log_id in enumerate(ascending, start=1):
        _insert_access_log(
            log_id=log_id,
            short_link_id=link["id"],
            domain_id=default_domain["id"],
            accessed_at=accessed_at,
            access_date=date(2026, 8, 16),
            country="CN" if index % 2 else "US",
            result="allowed" if index % 2 else "denied",
        )
    records = KeysetRecords(
        link_id=link["id"],
        name=name,
        descending_ids=(
            "40000000-0000-0000-0000-000000000005",
            "40000000-0000-0000-0000-000000000004",
            "40000000-0000-0000-0000-000000000003",
            "40000000-0000-0000-0000-000000000002",
            "40000000-0000-0000-0000-000000000001",
        ),
        accessed_at=accessed_at,
        domain_id=default_domain["id"],
    )
    yield records
    with _sync_engine.begin() as connection:
        connection.execute(
            text("DELETE FROM access_logs WHERE id::text LIKE '40000000-%'")
        )


async def test_keyset_pages_same_timestamp_by_descending_uuid_without_duplicates(
    client, admin_token, keyset_records
):
    cursor = None
    actual_ids = []
    while True:
        params = {"name": keyset_records.name, "limit": 2}
        if cursor:
            params["cursor"] = cursor
        response = await client.get(
            "/api/logs", params=params, headers={**_auth(admin_token), **_host()}
        )
        assert response.status_code == 200, response.text
        page = response.json()
        actual_ids.extend(row["id"] for row in page["items"])
        if not page["has_more"]:
            assert page["next_cursor"] is None
            break
        cursor = page["next_cursor"]
    assert tuple(actual_ids) == keyset_records.descending_ids
    assert len(actual_ids) == len(set(actual_ids))


async def test_cursor_boundary_is_normalized_from_a_non_utc_postgres_session(
    keyset_records,
):
    timezone_engine = create_async_engine(
        settings.database_url.replace("+asyncpg", "+psycopg"),
        poolclass=NullPool,
    )
    sessions = async_sessionmaker(timezone_engine, expire_on_commit=False)
    try:
        async with sessions() as db:
            await db.execute(text("SET TIME ZONE 'America/New_York'"))
            current_user = (
                await db.execute(select(User).where(User.username == "admin"))
            ).scalar_one()
            current_domain = (
                await db.execute(
                    select(Domain).where(Domain.id == UUID(keyset_records.domain_id))
                )
            ).scalar_one()
            database_timestamp = (
                await db.execute(
                    select(AccessLog.accessed_at).where(
                        AccessLog.id == UUID(keyset_records.descending_ids[0])
                    )
                )
            ).scalar_one()
            assert database_timestamp.utcoffset() != timedelta(0)

            filters = AccessLogFilters.from_values(
                short_link_id=None,
                short_code=None,
                name=keyset_records.name,
                date_from=None,
                date_to=None,
                countries=[],
                results=[],
            )
            page = await list_logs_query(
                db,
                current_user,
                current_domain,
                None,
                filters,
                2,
                None,
            )

            assert page.has_more is True
            assert page.next_cursor is not None
            boundary = decode_cursor(
                page.next_cursor,
                filters.digest_scope(current_domain.id, current_user),
                settings.secret_key,
            )
            assert boundary.accessed_at == keyset_records.accessed_at
            assert boundary.accessed_at.utcoffset() == timedelta(0)
    finally:
        await timezone_engine.dispose()


async def test_old_cursor_ignores_newer_insert_and_starts_after_page_tail(
    client, admin_token, keyset_records
):
    first = await client.get(
        "/api/logs",
        params={"name": keyset_records.name, "limit": 2},
        headers={**_auth(admin_token), **_host()},
    )
    assert first.status_code == 200
    page_one = first.json()
    newer_id = UUID("40000000-0000-0000-0001-000000000001")
    _insert_access_log(
        log_id=newer_id,
        short_link_id=keyset_records.link_id,
        domain_id=keyset_records.domain_id,
        accessed_at=keyset_records.accessed_at + timedelta(seconds=1),
        access_date=date(2026, 8, 16),
        country="CN",
        result="allowed",
    )
    second = await client.get(
        "/api/logs",
        params={
            "name": keyset_records.name,
            "limit": 2,
            "cursor": page_one["next_cursor"],
        },
        headers={**_auth(admin_token), **_host()},
    )
    assert second.status_code == 200
    second_ids = [row["id"] for row in second.json()["items"]]
    assert second_ids == list(keyset_records.descending_ids[2:4])
    assert str(newer_id) not in second_ids
    assert not set(second_ids).intersection(row["id"] for row in page_one["items"])


async def test_cursor_rejects_tampering_filter_change_and_user_change(
    client, admin_token, operator_token, keyset_records
):
    first = await client.get(
        "/api/logs",
        params={"name": keyset_records.name, "limit": 1},
        headers={**_auth(admin_token), **_host()},
    )
    cursor = first.json()["next_cursor"]
    assert cursor
    tampered = cursor[:-1] + ("A" if cursor[-1] != "A" else "B")
    cases = (
        (admin_token, {"name": keyset_records.name, "cursor": tampered}),
        (
            admin_token,
            {"name": keyset_records.name, "cursor": cursor, "country": "US"},
        ),
        (operator_token, {"name": keyset_records.name, "cursor": cursor}),
    )
    for token, params in cases:
        response = await client.get(
            "/api/logs", params=params, headers={**_auth(token), **_host()}
        )
        assert response.status_code == 400
        assert response.json() == {
            "code": "INVALID_CURSOR",
            "message": "无效的分页游标",
        }


async def test_cursor_accepts_equivalent_multivalue_order_duplicates_and_limit_change(
    client, admin_token, keyset_records
):
    first = await client.get(
        "/api/logs",
        params=[
            ("name", keyset_records.name),
            ("country", "CN"),
            ("country", "US"),
            ("country", "CN"),
            ("result", "allowed"),
            ("result", "denied"),
            ("result", "allowed"),
            ("limit", "1"),
        ],
        headers={**_auth(admin_token), **_host()},
    )
    assert first.status_code == 200
    cursor = first.json()["next_cursor"]
    second = await client.get(
        "/api/logs",
        params=[
            ("name", keyset_records.name),
            ("country", "US"),
            ("country", "CN"),
            ("result", "denied"),
            ("result", "allowed"),
            ("limit", "3"),
            ("cursor", cursor),
        ],
        headers={**_auth(admin_token), **_host()},
    )
    assert second.status_code == 200, second.text


async def test_client_cursor_rechecks_permissions_after_grant_revocation(
    client, admin_token, client_token, default_domain
):
    name = f"Revoked Page {uuid4().hex[:8]}"
    link = await _create_named_link(
        client,
        admin_token,
        default_domain["id"],
        alias=f"revoked-page-{uuid4().hex[:8]}",
        name=name,
    )
    client_id = (await client.get("/api/auth/me", headers=_auth(client_token))).json()[
        "id"
    ]
    grant = await client.post(
        f"/api/short-links/{link['id']}/permissions",
        headers={**_auth(admin_token), **_host()},
        json={"user_id": client_id},
    )
    assert grant.status_code == 200
    for number in range(1, 4):
        _insert_access_log(
            log_id=uuid4(),
            short_link_id=link["id"],
            domain_id=default_domain["id"],
            accessed_at=datetime(2026, 8, 16, number, tzinfo=timezone.utc),
            access_date=date(2026, 8, 16),
            country="CN",
            result="allowed",
        )
    first = await client.get(
        "/api/logs",
        params={"name": name, "limit": 1},
        headers={**_auth(client_token), **_host()},
    )
    assert first.status_code == 200
    cursor = first.json()["next_cursor"]
    revoked = await client.delete(
        f"/api/short-links/{link['id']}/permissions/{client_id}",
        headers={**_auth(admin_token), **_host()},
    )
    assert revoked.status_code == 200
    second = await client.get(
        "/api/logs",
        params={"name": name, "limit": 2, "cursor": cursor},
        headers={**_auth(client_token), **_host()},
    )
    assert second.status_code == 200
    assert second.json() == {"items": [], "next_cursor": None, "has_more": False}
