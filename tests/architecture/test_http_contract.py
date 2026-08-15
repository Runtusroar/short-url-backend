from app.main import app


EXPECTED_ROUTES = {
    ("POST", "/api/auth/login", "login"),
    ("POST", "/api/auth/login-cookie", "login_cookie"),
    ("POST", "/api/auth/logout", "logout"),
    ("GET", "/api/auth/me", "me"),
    ("GET", "/api/short-links", "list_short_links"),
    ("POST", "/api/short-links", "create_short_link"),
    ("GET", "/api/short-links/daily-stats", "daily_stats_for_links"),
    ("GET", "/api/short-links/{link_id}", "get_short_link"),
    ("PUT", "/api/short-links/{link_id}", "update_short_link"),
    ("DELETE", "/api/short-links/{link_id}", "delete_short_link"),
    ("POST", "/api/short-links/{link_id}/urls", "add_target_url"),
    ("PUT", "/api/short-links/{link_id}/urls/{url_id}", "update_target_url"),
    ("DELETE", "/api/short-links/{link_id}/urls/{url_id}", "delete_target_url"),
    ("POST", "/api/short-links/{link_id}/rules", "add_access_rule"),
    ("PUT", "/api/short-links/{link_id}/rules/{rule_id}", "update_access_rule"),
    ("DELETE", "/api/short-links/{link_id}/rules/{rule_id}", "delete_access_rule"),
    ("POST", "/api/short-links/{link_id}/permissions", "grant_permission"),
    ("DELETE", "/api/short-links/{link_id}/permissions/{user_id}", "revoke_permission"),
    ("GET", "/api/logs", "list_logs"),
    ("GET", "/api/logs/daily", "daily_stats"),
    ("GET", "/api/logs/daily-summary", "daily_summary"),
    ("GET", "/api/admin/users", "list_users"),
    ("POST", "/api/admin/users", "create_user"),
    ("PUT", "/api/admin/users/{user_id}", "update_user"),
    ("DELETE", "/api/admin/users/{user_id}", "delete_user"),
    ("GET", "/api/ip-blacklist", "list_blacklist"),
    ("POST", "/api/ip-blacklist", "add_to_blacklist"),
    ("DELETE", "/api/ip-blacklist/{entry_id}", "remove_from_blacklist"),
    ("GET", "/api/domains", "list_domains"),
    ("POST", "/api/domains", "create_domain"),
    ("GET", "/api/domains/{domain_id}", "get_domain"),
    ("PUT", "/api/domains/{domain_id}", "update_domain"),
    ("DELETE", "/api/domains/{domain_id}", "delete_domain"),
    ("GET", "/health", "health"),
    ("GET", "/health/live", "health"),
    ("GET", "/health/ready", "readiness"),
    ("GET", "/{short_code}", "redirect"),
    ("HEAD", "/{short_code}", "redirect"),
}


def test_public_http_route_contract_is_stable():
    actual = {
        (method, route.path, route.name)
        for route in app.routes
        for method in (route.methods or set())
        if route.path not in {"/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc"}
    }
    assert actual == EXPECTED_ROUTES
