from collections import Counter

from app.main import app


EXPECTED = {
    ("GET", "/health"),
    ("POST", "/api/auth/login"),
    ("POST", "/api/auth/login-cookie"),
    ("POST", "/api/auth/logout"),
    ("GET", "/api/auth/me"),
    ("GET", "/api/dashboard"),
    ("GET", "/api/short-links"),
    ("POST", "/api/short-links"),
    ("GET", "/api/short-links/{link_id}"),
    ("PUT", "/api/short-links/{link_id}"),
    ("DELETE", "/api/short-links/{link_id}"),
    ("GET", "/api/access-logs"),
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
    ("GET", "/{short_code}"),
    ("HEAD", "/{short_code}"),
}


def test_no_legacy_api_routes_and_final_routes_are_registered_once():
    """A legacy router or duplicate final inclusion must fail this public contract."""
    routes = [
        (method, route.path)
        for route in app.routes
        for method in route.methods or set()
    ]
    paths = set(routes)

    assert EXPECTED <= paths
    assert not any(path.startswith("/api/admin") for _, path in paths)
    assert not any(
        path.startswith("/api/logs")
        or path.startswith("/api/ip-blacklist")
        or "/permissions" in path
        or "/rules" in path
        for _, path in paths
    )
    assert all(routes.count(route) == 1 for route in EXPECTED)


def test_final_route_schema_has_unique_operation_ids():
    """A shared GET/HEAD handler must not leave clients an ambiguous OpenAPI operation ID."""
    operations = [
        operation["operationId"]
        for path in app.openapi()["paths"].values()
        for operation in path.values()
        if isinstance(operation, dict) and "operationId" in operation
    ]

    assert len(operations) == len(set(operations))
