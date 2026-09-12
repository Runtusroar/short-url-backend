import asyncio
import uuid
from types import SimpleNamespace

import pytest
from asyncpg.exceptions import UniqueViolationError
from sqlalchemy.dialects.postgresql.asyncpg import AsyncAdapt_asyncpg_dbapi
from sqlalchemy.exc import IntegrityError

from app.api import short_links as short_links_api
from app.db.session import AsyncSessionLocal
from app.db.models import ShortLink


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def aggregate_payload(domain_id: str, **overrides) -> dict[str, object]:
    payload: dict[str, object] = {
        "domain_id": domain_id,
        "custom_alias": "CampaignA",
        "note": "Autumn campaign",
        "is_active": True,
        "destinations": [
            {
                "url": "https://example.com/ok",
                "type": "allowed",
                "weight": 1,
                "is_active": True,
            },
            {
                "url": "https://example.com/blocked",
                "type": "blocked",
                "weight": 1,
                "is_active": True,
            },
        ],
        "policy": {
            "country_mode": "allow",
            "countries": ["cn", "SG"],
            "platform_mode": "off",
            "platforms": [],
            "referer_mode": "allow",
            "referer_patterns": ["*.Example.COM"],
            "block_proxy": True,
            "block_bot": True,
        },
    }
    payload.update(overrides)
    return payload


@pytest.mark.asyncio
async def test_manage_user_creates_complete_normalized_short_link(client, operator_token, domain_a):
    response = await client.post(
        "/api/short-links",
        headers=auth(operator_token),
        json=aggregate_payload(str(domain_a.id), custom_alias="CampaignNormA"),
    )

    assert response.status_code == 201, response.text
    link = response.json()
    assert link["short_code"] == "CampaignNormA"
    assert link["note"] == "Autumn campaign"
    assert {destination["type"] for destination in link["destinations"]} == {"allowed", "blocked"}
    assert link["policy"]["countries"] == ["CN", "SG"]
    assert link["policy"]["referer_patterns"] == ["*.example.com"]
    assert link["policy"]["block_proxy"] is True


@pytest.mark.asyncio
async def test_policy_values_normalize_valid_country_and_hostname_patterns(client, operator_token, domain_a):
    response = await client.post(
        "/api/short-links",
        headers=auth(operator_token),
        json=aggregate_payload(
            str(domain_a.id),
            custom_alias="PolicyNormalizeA",
            policy={
                "country_mode": "allow",
                "countries": ["cN"],
                "platform_mode": "off",
                "platforms": [],
                "referer_mode": "allow",
                "referer_patterns": ["Example.COM", "*.Sub.Example.COM"],
                "block_proxy": False,
                "block_bot": False,
            },
        ),
    )

    assert response.status_code == 201, response.text
    assert response.json()["policy"]["countries"] == ["CN"]
    assert response.json()["policy"]["referer_patterns"] == [
        "example.com",
        "*.sub.example.com",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("countries", "referer_patterns"),
    [
        (["ſſ"], ["example.com"]),
        (["C"], ["example.com"]),
        (["USA"], ["example.com"]),
        (["CN"], ["https://example.com"]),
        (["CN"], ["example.com:443"]),
        (["CN"], ["example.com/path"]),
        (["CN"], ["example.com?query=1"]),
        (["CN"], ["example.com#fragment"]),
        (["CN"], ["example..com"]),
        (["CN"], ["exam*ple.com"]),
        (["CN"], ["*example.com"]),
        (["CN"], ["Kexample.com"]),
    ],
)
async def test_policy_rejects_noncanonical_country_and_referer_values(
    client, operator_token, domain_a, countries, referer_patterns
):
    response = await client.post(
        "/api/short-links",
        headers=auth(operator_token),
        json=aggregate_payload(
            str(domain_a.id),
            custom_alias=f"PolicyInvalid{uuid.uuid4().hex[:12]}",
            policy={
                "country_mode": "allow",
                "countries": countries,
                "platform_mode": "off",
                "platforms": [],
                "referer_mode": "allow",
                "referer_patterns": referer_patterns,
                "block_proxy": False,
                "block_bot": False,
            },
        ),
    )

    assert response.status_code == 422, response.text
    assert response.json()["code"] == "VALIDATION_ERROR"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("destinations", "policy"),
    [
        (
            [
                {
                    "url": "javascript:alert(1)",
                    "type": "allowed",
                    "weight": 1,
                    "is_active": True,
                }
            ],
            None,
        ),
        (
            [
                {
                    "url": "https:// example.com/path",
                    "type": "allowed",
                    "weight": 1,
                    "is_active": True,
                }
            ],
            None,
        ),
        (
            None,
            {
                "country_mode": "off",
                "countries": [],
                "platform_mode": "allow",
                "platforms": ["mobile"],
                "referer_mode": "off",
                "referer_patterns": [],
                "block_proxy": False,
                "block_bot": False,
            },
        ),
    ],
)
async def test_short_link_contract_rejects_unsafe_destinations_and_unknown_platforms(
    client, operator_token, domain_a, destinations, policy
):
    """Schema validation must surface unsafe redirect inputs as the standard 422 envelope."""
    overrides = {"custom_alias": f"Unsafe{uuid.uuid4().hex[:12]}"}
    if destinations is not None:
        overrides["destinations"] = destinations
    if policy is not None:
        overrides["policy"] = policy
    response = await client.post(
        "/api/short-links", headers=auth(operator_token), json=aggregate_payload(str(domain_a.id), **overrides)
    )

    assert response.status_code == 422, response.text
    assert response.json()["code"] == "VALIDATION_ERROR"


@pytest.mark.asyncio
async def test_read_access_can_list_and_detail_but_cannot_mutate(client, admin_token, read_token, domain_a):
    created = await client.post(
        "/api/short-links", headers=auth(admin_token), json=aggregate_payload(str(domain_a.id), custom_alias="ReadAccessA")
    )
    assert created.status_code == 201, created.text
    link_id = created.json()["id"]

    listed = await client.get(f"/api/short-links?domain_id={domain_a.id}", headers=auth(read_token))
    assert listed.status_code == 200, listed.text
    assert any(item["id"] == link_id for item in listed.json()["items"])

    detailed = await client.get(f"/api/short-links/{link_id}", headers=auth(read_token))
    assert detailed.status_code == 200, detailed.text

    denied = await client.put(
        f"/api/short-links/{link_id}",
        headers=auth(read_token),
        json=aggregate_payload(str(domain_a.id), custom_alias=None, note="not allowed"),
    )
    assert denied.status_code == 403
    assert denied.json()["code"] == "PERMISSION_DENIED"


@pytest.mark.asyncio
async def test_manage_access_is_domain_scoped_not_owner_scoped(client, operator_token, client_token, domain_a):
    created = await client.post(
        "/api/short-links", headers=auth(operator_token), json=aggregate_payload(str(domain_a.id), custom_alias="OwnerScopeA")
    )
    assert created.status_code == 201, created.text

    updated = await client.put(
        f"/api/short-links/{created.json()['id']}",
        headers=auth(client_token),
        json=aggregate_payload(str(domain_a.id), custom_alias=None, note="managed by another user"),
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["owner_id"] == created.json()["owner_id"]
    assert updated.json()["note"] == "managed by another user"


@pytest.mark.asyncio
async def test_cross_domain_link_id_is_not_disclosed(client, admin_token, manage_token, domain_a):
    created = await client.post(
        "/api/short-links", headers=auth(admin_token), json=aggregate_payload(str(domain_a.id), custom_alias="CrossDomainA")
    )
    assert created.status_code == 201, created.text

    requests = (
        client.get(f"/api/short-links/{created.json()['id']}", headers=auth(manage_token)),
        client.put(
            f"/api/short-links/{created.json()['id']}",
            headers=auth(manage_token),
            json=aggregate_payload(str(domain_a.id), custom_alias=None),
        ),
        client.delete(f"/api/short-links/{created.json()['id']}", headers=auth(manage_token)),
    )
    for request in requests:
        response = await request
        assert response.status_code == 404, response.text
        assert response.json()["code"] == "NOT_FOUND"


@pytest.mark.asyncio
async def test_delete_short_link_succeeds(client, admin_token, domain_a):
    created = await client.post(
        "/api/short-links",
        headers=auth(admin_token),
        json=aggregate_payload(str(domain_a.id), custom_alias="DeleteSuccessA"),
    )
    assert created.status_code == 201, created.text

    deleted = await client.delete(
        f"/api/short-links/{created.json()['id']}", headers=auth(admin_token)
    )

    assert deleted.status_code == 204
    assert deleted.content == b""


@pytest.mark.asyncio
async def test_invalid_complete_update_rolls_back_existing_children(client, admin_token, domain_a):
    created = await client.post(
        "/api/short-links", headers=auth(admin_token), json=aggregate_payload(str(domain_a.id), custom_alias="RollbackA")
    )
    assert created.status_code == 201, created.text
    link = created.json()

    invalid = await client.put(
        f"/api/short-links/{link['id']}",
        headers=auth(admin_token),
        json=aggregate_payload(
            str(domain_a.id),
            custom_alias=None,
            note="must not persist",
            destinations=[
                {
                    "id": link["destinations"][0]["id"],
                    "url": "https://example.com/no-longer-active",
                    "type": "allowed",
                    "weight": 1,
                    "is_active": False,
                }
            ],
        ),
    )
    assert invalid.status_code == 422

    unchanged = await client.get(f"/api/short-links/{link['id']}", headers=auth(admin_token))
    assert unchanged.status_code == 200, unchanged.text
    assert unchanged.json()["note"] == "Autumn campaign"
    assert {destination["url"] for destination in unchanged.json()["destinations"]} == {
        "https://example.com/ok",
        "https://example.com/blocked",
    }


@pytest.mark.asyncio
async def test_aggregate_update_replaces_children_by_id_and_upserts_policy(client, admin_token, domain_a):
    created = await client.post(
        "/api/short-links", headers=auth(admin_token), json=aggregate_payload(str(domain_a.id), custom_alias="ReplaceChildrenA")
    )
    assert created.status_code == 201, created.text
    original = created.json()
    allowed = next(item for item in original["destinations"] if item["type"] == "allowed")
    blocked = next(item for item in original["destinations"] if item["type"] == "blocked")

    updated = await client.put(
        f"/api/short-links/{original['id']}",
        headers=auth(admin_token),
        json=aggregate_payload(
            str(domain_a.id),
            custom_alias=None,
            note="replaced aggregate",
            destinations=[
                {
                    "id": allowed["id"],
                    "url": "https://example.com/changed",
                    "type": "allowed",
                    "weight": 9,
                    "is_active": True,
                },
                {
                    "url": "https://example.com/new-blocked",
                    "type": "blocked",
                    "weight": 2,
                    "is_active": True,
                },
            ],
            policy={
                "country_mode": "block",
                "countries": ["us"],
                "platform_mode": "allow",
                "platforms": ["desktop"],
                "referer_mode": "off",
                "referer_patterns": [],
                "block_proxy": False,
                "block_bot": False,
            },
        ),
    )

    assert updated.status_code == 200, updated.text
    result = updated.json()
    assert result["note"] == "replaced aggregate"
    assert result["policy"]["country_mode"] == "block"
    assert result["policy"]["countries"] == ["US"]
    assert result["policy"]["platform_mode"] == "allow"
    assert result["policy"]["platforms"] == ["desktop"]
    assert {item["id"] for item in result["destinations"]} != {allowed["id"], blocked["id"]}
    assert any(
        item["id"] == allowed["id"]
        and item["url"] == "https://example.com/changed"
        and item["weight"] == 9
        for item in result["destinations"]
    )
    assert all(item["id"] != blocked["id"] for item in result["destinations"])
    assert any(item["url"] == "https://example.com/new-blocked" for item in result["destinations"])


@pytest.mark.asyncio
async def test_unknown_destination_update_rolls_back_scalar_and_aggregate_changes(client, admin_token, domain_a):
    created = await client.post(
        "/api/short-links", headers=auth(admin_token), json=aggregate_payload(str(domain_a.id), custom_alias="UnknownRollbackA")
    )
    assert created.status_code == 201, created.text
    original = created.json()
    allowed = next(item for item in original["destinations"] if item["type"] == "allowed")

    rejected = await client.put(
        f"/api/short-links/{original['id']}",
        headers=auth(admin_token),
        json=aggregate_payload(
            str(domain_a.id),
            custom_alias=None,
            note="must roll back inside transaction",
            destinations=[
                {
                    "id": allowed["id"],
                    "url": "https://example.com/changed-before-error",
                    "type": "allowed",
                    "weight": 4,
                    "is_active": True,
                },
                {
                    "id": str(uuid.uuid4()),
                    "url": "https://example.com/not-owned",
                    "type": "blocked",
                    "weight": 1,
                    "is_active": True,
                },
            ],
            policy={
                "country_mode": "block",
                "countries": ["US"],
                "platform_mode": "off",
                "platforms": [],
                "referer_mode": "off",
                "referer_patterns": [],
                "block_proxy": False,
                "block_bot": False,
            },
        ),
    )

    assert rejected.status_code == 404, rejected.text
    assert rejected.json()["code"] == "NOT_FOUND"

    unchanged = await client.get(f"/api/short-links/{original['id']}", headers=auth(admin_token))
    assert unchanged.status_code == 200, unchanged.text
    assert unchanged.json()["note"] == original["note"]
    assert unchanged.json()["destinations"] == original["destinations"]
    assert unchanged.json()["policy"] == original["policy"]


@pytest.mark.asyncio
async def test_alias_conflicts_are_stable_and_case_sensitive(client, operator_token, domain_a):
    first = await client.post(
        "/api/short-links", headers=auth(operator_token), json=aggregate_payload(str(domain_a.id), custom_alias="CaseConflictA")
    )
    assert first.status_code == 201, first.text

    duplicate = await client.post(
        "/api/short-links", headers=auth(operator_token), json=aggregate_payload(str(domain_a.id), custom_alias="CaseConflictA")
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["code"] == "SHORT_CODE_CONFLICT"

    differently_cased = await client.post(
        "/api/short-links",
        headers=auth(operator_token),
        json=aggregate_payload(str(domain_a.id), custom_alias="caseconflicta"),
    )
    assert differently_cased.status_code == 201, differently_cased.text


@pytest.mark.asyncio
async def test_non_short_code_integrity_error_is_not_reported_as_a_short_code_conflict(
    client, operator_token, domain_a, monkeypatch
):
    """Only uq_domain_short_code may become the public short-code conflict envelope."""

    async def fail_with_an_unrelated_constraint(*_args, **_kwargs):
        raise IntegrityError(
            "INSERT INTO target_urls",
            {},
            SimpleNamespace(diag=SimpleNamespace(constraint_name="target_urls_url_key")),
        )

    monkeypatch.setattr(short_links_api, "_replace_destinations", fail_with_an_unrelated_constraint)

    response = await client.post(
        "/api/short-links",
        headers=auth(operator_token),
        json=aggregate_payload(str(domain_a.id), custom_alias="OtherIntegrityA"),
    )

    assert response.status_code == 409
    assert response.json()["code"] == "CONFLICT"


def _asyncpg_unique_violation(constraint_name: str) -> UniqueViolationError:
    error = UniqueViolationError("duplicate key value violates unique constraint")
    error.constraint_name = constraint_name
    return error


def _adapted_asyncpg_unique_violation(
    constraint_name: str, *, chain_attribute: str = "__cause__"
) -> BaseException:
    driver_error = _asyncpg_unique_violation(constraint_name)
    adapted_error = AsyncAdapt_asyncpg_dbapi.IntegrityError("duplicate key value")
    setattr(adapted_error, chain_attribute, driver_error)
    return adapted_error


@pytest.mark.parametrize(
    ("driver_error", "expected"),
    [
        pytest.param(
            SimpleNamespace(diag=SimpleNamespace(constraint_name="uq_domain_short_code")),
            True,
            id="psycopg-diagnostic",
        ),
        pytest.param(
            _asyncpg_unique_violation("uq_domain_short_code"),
            True,
            id="asyncpg-diagnostic",
        ),
        pytest.param(
            _asyncpg_unique_violation("target_urls_url_key"),
            False,
            id="asyncpg-unrelated-constraint",
        ),
        pytest.param(
            _adapted_asyncpg_unique_violation("uq_domain_short_code"),
            True,
            id="sqlalchemy-asyncpg-adapted-cause",
        ),
        pytest.param(
            _adapted_asyncpg_unique_violation(
                "uq_domain_short_code", chain_attribute="__context__"
            ),
            True,
            id="sqlalchemy-asyncpg-adapted-context",
        ),
        pytest.param(
            _adapted_asyncpg_unique_violation("target_urls_url_key"),
            False,
            id="sqlalchemy-asyncpg-adapted-unrelated",
        ),
    ],
)
def test_short_code_constraint_classification_is_driver_independent(driver_error, expected):
    """Both supported PostgreSQL drivers expose the named constraint without parsing text."""
    error = IntegrityError("INSERT", {}, driver_error)

    assert short_links_api._is_short_code_unique_violation(error) is expected


@pytest.mark.asyncio
async def test_psycopg_alias_constraint_uses_its_named_diagnostic(domain_a):
    """The test database's psycopg exception supplies its constraint through ``diag``."""
    code = f"Psycopg{uuid.uuid4().hex[:18]}"
    async with AsyncSessionLocal() as session:
        session.add(ShortLink(domain_id=domain_a.id, short_code=code))
        await session.commit()

    async with AsyncSessionLocal() as session:
        session.add(ShortLink(domain_id=domain_a.id, short_code=code))
        with pytest.raises(IntegrityError) as raised:
            await session.flush()
        assert short_links_api._is_short_code_unique_violation(raised.value)
        await session.rollback()


@pytest.mark.asyncio
async def test_asyncpg_alias_constraint_returns_the_public_short_code_conflict(
    client, operator_token, domain_a, monkeypatch
):
    """Production uses asyncpg, so its direct constraint attribute must map to the alias envelope."""

    async def fail_with_asyncpg_alias_constraint(*_args, **_kwargs):
        raise IntegrityError(
            "INSERT INTO short_links",
            {},
            _asyncpg_unique_violation("uq_domain_short_code"),
        )

    monkeypatch.setattr(short_links_api, "_replace_destinations", fail_with_asyncpg_alias_constraint)

    response = await client.post(
        "/api/short-links",
        headers=auth(operator_token),
        json=aggregate_payload(str(domain_a.id), custom_alias="AsyncpgConflictA"),
    )

    assert response.status_code == 409
    assert response.json()["code"] == "SHORT_CODE_CONFLICT"


@pytest.mark.asyncio
async def test_adapted_asyncpg_alias_constraint_returns_the_public_short_code_conflict(
    client, operator_token, domain_a, monkeypatch
):
    """SQLAlchemy's asyncpg adapter keeps the server diagnostic in its exception chain."""

    async def fail_with_adapted_asyncpg_alias_constraint(*_args, **_kwargs):
        raise IntegrityError(
            "INSERT INTO short_links",
            {},
            _adapted_asyncpg_unique_violation("uq_domain_short_code"),
        )

    monkeypatch.setattr(short_links_api, "_replace_destinations", fail_with_adapted_asyncpg_alias_constraint)

    response = await client.post(
        "/api/short-links",
        headers=auth(operator_token),
        json=aggregate_payload(str(domain_a.id), custom_alias="AdaptedAsyncpgConflictA"),
    )

    assert response.status_code == 409
    assert response.json()["code"] == "SHORT_CODE_CONFLICT"


@pytest.mark.asyncio
async def test_simultaneous_alias_requests_leave_one_link_and_a_stable_conflict(
    client, operator_token, domain_a
):
    payload = aggregate_payload(str(domain_a.id), custom_alias="ConcurrentAliasA")
    first, second = await asyncio.gather(
        client.post("/api/short-links", headers=auth(operator_token), json=payload),
        client.post("/api/short-links", headers=auth(operator_token), json=payload),
    )

    assert sorted(response.status_code for response in (first, second)) == [201, 409]
    conflict = next(response for response in (first, second) if response.status_code == 409)
    assert conflict.json()["code"] == "SHORT_CODE_CONFLICT"

    listed = await client.get(
        f"/api/short-links?domain_id={domain_a.id}&keyword=ConcurrentAliasA", headers=auth(operator_token)
    )
    assert listed.status_code == 200, listed.text
    assert listed.json()["total"] == 1


@pytest.mark.asyncio
async def test_short_link_listing_filters_and_paginates(client, admin_token, domain_a):
    first = await client.post(
        "/api/short-links", headers=auth(admin_token), json=aggregate_payload(str(domain_a.id), custom_alias="CampaignListA")
    )
    second = await client.post(
        "/api/short-links",
        headers=auth(admin_token),
        json=aggregate_payload(str(domain_a.id), custom_alias="CampaignListB", note="Winter campaign", is_active=False),
    )
    assert first.status_code == second.status_code == 201

    response = await client.get(
        f"/api/short-links?domain_id={domain_a.id}&keyword=Winter&is_active=false&page=1&page_size=1",
        headers=auth(admin_token),
    )
    assert response.status_code == 200, response.text
    assert response.json()["total"] == 1
    assert response.json()["items"][0]["short_code"] == "CampaignListB"
