import asyncio

import pytest


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
