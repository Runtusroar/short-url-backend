from collections import Counter
from importlib import import_module
from uuid import uuid4

import pytest
from fastapi.routing import APIRoute

from app.main import app


BUSINESS_ROUTES = (
    ("POST", "/api/auth/login"),
    ("POST", "/api/auth/login-cookie"),
    ("POST", "/api/auth/logout"),
    ("GET", "/api/auth/me"),
    ("GET", "/api/users"),
    ("POST", "/api/users"),
    ("PUT", "/api/users/{user_id}"),
    ("GET", "/api/domains"),
    ("POST", "/api/domains"),
    ("PUT", "/api/domains/{domain_id}"),
    ("DELETE", "/api/domains/{domain_id}"),
    ("GET", "/api/security/ip-blacklist"),
    ("POST", "/api/security/ip-blacklist"),
    ("PUT", "/api/security/ip-blacklist/{entry_id}"),
    ("DELETE", "/api/security/ip-blacklist/{entry_id}"),
    ("GET", "/api/short-links"),
    ("GET", "/api/short-links/{link_id}"),
    ("POST", "/api/short-links"),
    ("PUT", "/api/short-links/{link_id}"),
    ("DELETE", "/api/short-links/{link_id}"),
    ("GET", "/api/dashboard"),
    ("GET", "/api/access-logs"),
    ("GET", "/health"),
    ("GET", "/{short_code}"),
    ("HEAD", "/{short_code}"),
)
BUSINESS_ROUTE_COVERAGE = set(BUSINESS_ROUTES)


# This matrix is deliberately explicit: one test name per business route prevents a
# router from becoming an untested, implicitly registered public API.
ROUTE_COVERAGE = {
    ("GET", "/health"): ("tests/api/test_route_contract.py::test_health_returns_service_status",),
    ("POST", "/api/auth/login"): ("tests/api/test_auth.py::test_login_rejects_wrong_password",),
    ("POST", "/api/auth/login-cookie"): ("tests/api/test_auth.py::test_login_cookie_uses_configured_security_attributes",),
    ("POST", "/api/auth/logout"): ("tests/api/test_auth.py::test_logout_succeeds_and_clears_cookie",),
    ("GET", "/api/auth/me"): ("tests/api/test_auth.py::test_me_returns_the_authenticated_user",),
    ("GET", "/api/users"): ("tests/api/test_users.py::test_admin_can_create_list_update_and_deactivate_user_with_replaced_grants",),
    ("POST", "/api/users"): ("tests/api/test_users.py::test_admin_can_create_list_update_and_deactivate_user_with_replaced_grants",),
    ("PUT", "/api/users/{user_id}"): ("tests/api/test_users.py::test_admin_can_create_list_update_and_deactivate_user_with_replaced_grants",),
    ("GET", "/api/domains"): ("tests/api/test_domains.py::test_authenticated_domain_listing_keeps_inactive_domains_admin_visible_and_subaccounts_scoped",),
    ("POST", "/api/domains"): ("tests/api/test_domains.py::test_admin_can_create_update_and_delete_domain",),
    ("PUT", "/api/domains/{domain_id}"): ("tests/api/test_domains.py::test_admin_can_create_update_and_delete_domain",),
    ("DELETE", "/api/domains/{domain_id}"): ("tests/api/test_domains.py::test_admin_can_create_update_and_delete_domain",),
    ("GET", "/api/security/ip-blacklist"): ("tests/api/test_security.py::test_admin_can_create_list_update_and_delete_global_ip_blacklist_entry",),
    ("POST", "/api/security/ip-blacklist"): ("tests/api/test_security.py::test_admin_can_create_list_update_and_delete_global_ip_blacklist_entry",),
    ("PUT", "/api/security/ip-blacklist/{entry_id}"): ("tests/api/test_security.py::test_admin_can_create_list_update_and_delete_global_ip_blacklist_entry",),
    ("DELETE", "/api/security/ip-blacklist/{entry_id}"): ("tests/api/test_security.py::test_admin_can_create_list_update_and_delete_global_ip_blacklist_entry",),
    ("GET", "/api/short-links"): ("tests/api/test_short_links.py::test_short_link_listing_filters_and_paginates",),
    ("POST", "/api/short-links"): ("tests/api/test_short_links.py::test_manage_user_creates_complete_normalized_short_link",),
    ("GET", "/api/short-links/{link_id}"): ("tests/api/test_short_links.py::test_read_access_can_list_and_detail_but_cannot_mutate",),
    ("PUT", "/api/short-links/{link_id}"): ("tests/api/test_short_links.py::test_aggregate_update_replaces_children_by_id_and_upserts_policy",),
    ("DELETE", "/api/short-links/{link_id}"): ("tests/api/test_short_links.py::test_delete_short_link_succeeds",),
    ("GET", "/api/dashboard"): ("tests/api/test_dashboard.py::test_dashboard_aggregates_only_the_authorized_domain_without_n_plus_one_payloads",),
    ("GET", "/api/access-logs"): ("tests/api/test_logs.py::test_access_logs_scope_before_filters_and_return_complete_snapshots",),
    ("GET", "/{short_code}"): ("tests/api/test_redirect.py::test_unknown_or_inactive_hosts_and_links_do_not_redirect",),
    ("HEAD", "/{short_code}"): ("tests/api/test_redirect.py::test_head_matches_get_redirect_headers_without_a_response_body",),
}


def _registered_business_routes() -> list[tuple[str, str]]:
    return [
        (method, route.path)
        for route in app.routes
        if isinstance(route, APIRoute)
        for method in sorted(route.methods or set())
    ]


@pytest.mark.asyncio
async def test_health_returns_service_status(client):
    response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_no_legacy_api_routes_and_final_routes_are_registered_once():
    """Only the final business API may be registered, exactly once, in its public order."""
    routes = _registered_business_routes()

    assert routes == list(BUSINESS_ROUTES)
    assert Counter(routes) == Counter(BUSINESS_ROUTE_COVERAGE)
    assert len(routes) == len(set(routes))
    assert routes[-2:] == [("GET", "/{short_code}"), ("HEAD", "/{short_code}")]


def test_route_coverage_matrix_matches_the_exact_business_route_contract():
    assert set(ROUTE_COVERAGE) == BUSINESS_ROUTE_COVERAGE


@pytest.mark.parametrize("route", sorted(BUSINESS_ROUTE_COVERAGE))
def test_every_registered_business_route_has_an_explicit_coverage_mapping(route):
    for node_id in ROUTE_COVERAGE[route]:
        path, separator, test_name = node_id.partition("::")
        assert path.startswith("tests/api/")
        assert separator == "::"
        module = import_module(path.removesuffix(".py").replace("/", "."))
        assert callable(getattr(module, test_name, None))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("path", "params"),
    [
        ("/api/domains", {}),
        ("/api/short-links", {}),
        ("/api/dashboard", {"domain_id": str(uuid4())}),
        ("/api/access-logs", {"domain_id": str(uuid4())}),
    ],
)
async def test_data_routes_reject_unauthenticated_requests(client, path, params):
    response = await client.get(path, params=params)

    assert response.status_code == 401


def test_final_route_schema_has_unique_operation_ids():
    """A shared GET/HEAD handler must not leave clients an ambiguous OpenAPI operation ID."""
    operations = [
        operation["operationId"]
        for path in app.openapi()["paths"].values()
        for operation in path.values()
        if isinstance(operation, dict) and "operationId" in operation
    ]

    assert len(operations) == len(set(operations))
