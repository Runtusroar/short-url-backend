# Feature Package Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the flat `app` layout with the approved `core`, `models`, `features`, and `integrations` package boundaries without changing HTTP behavior, database metadata, configuration behavior, or access decisions.

**Architecture:** Infrastructure that is shared by every feature moves into `app/core`; ORM declarations move into a re-exporting `app/models` package; HTTP schemas, routers, and business workflows move together under `app/features`; Redis and MaxMind adapters move into `app/integrations`. Each move is guarded by route-contract, import-boundary, and SQLAlchemy metadata tests so the application remains deployable after every commit.

**Tech Stack:** Python 3.11, FastAPI 0.111, Pydantic 2, SQLAlchemy 2 async, Alembic, Redis 5, pytest 8

## Global Constraints

- This phase changes module ownership and imports only; it does not change database tables, columns, constraints, indexes, or migration history.
- Existing HTTP paths, methods, route names, request schemas, response schemas, status codes, cookies, and error payloads remain compatible.
- Existing authentication, authorization, domain scoping, redirect, logging, rate-limit, GeoIP, Bot, and proxy behavior remains unchanged.
- `Base.metadata.tables` and every mapped table's columns, foreign keys, constraints, defaults, relationships, and delete behavior remain identical.
- Router modules handle HTTP extraction, dependencies, and response conversion; service modules own workflows, queries, and transaction boundaries.
- ORM models are re-exported from `app.models`; callers do not import concrete model files unless a type-only dependency requires it.
- Pydantic schemas live in the feature that owns the HTTP contract.
- MaxMind and Redis adapters live under `app.integrations`.
- No compatibility facade is left at `app.auth`, `app.config`, `app.database`, `app.dependencies`, `app.domains`, `app.exceptions`, `app.rate_limit`, `app.schemas`, `app.routers`, or `app.services` after the phase completes.
- Do not add dependencies, Alembic revisions, schema fields, MaxMind Insights calls, new log filters, audit events, or CI/CD.
- Every task follows red-green-refactor, runs relevant regression tests, and ends with an isolated commit.

---

## Final File Map

```text
app/
├── main.py
├── core/
│   ├── __init__.py
│   ├── client_ip.py
│   ├── config.py
│   ├── database.py
│   ├── exceptions.py
│   ├── rate_limit.py
│   └── security.py
├── models/
│   ├── __init__.py
│   ├── access_log.py
│   ├── access_rule.py
│   ├── base.py
│   ├── blacklist.py
│   ├── domain.py
│   ├── short_link.py
│   └── user.py
├── features/
│   ├── auth/{__init__.py,router.py,schemas.py,service.py}
│   ├── users/{__init__.py,router.py,schemas.py,service.py}
│   ├── domains/{__init__.py,dependencies.py,router.py,schemas.py,service.py}
│   ├── blacklist/{__init__.py,router.py,schemas.py,service.py}
│   ├── short_links/{__init__.py,router.py,schemas.py,service.py,short_code.py}
│   ├── access_logs/{__init__.py,router.py,schemas.py,service.py}
│   └── redirect/{__init__.py,router.py,service.py,ua.py}
└── integrations/
    ├── __init__.py
    ├── redis.py
    └── maxmind/{__init__.py,country.py}
```

`tests/conftest.py` stays at the test root; behavior tests move to matching `tests/core`, `tests/features`, `tests/integrations`, and `tests/deployment` packages in Task 8.

---

### Task 1: Establish Core Infrastructure Boundaries

**Files:**
- Create: `app/core/config.py`
- Create: `app/core/database.py`
- Create: `app/core/security.py`
- Create: `app/core/exceptions.py`
- Create: `app/core/rate_limit.py`
- Modify: `app/core/client_ip.py`
- Modify: `app/main.py`
- Modify: all current application, test, Alembic, and script imports of the moved modules
- Delete: `app/auth.py`, `app/config.py`, `app/database.py`, `app/dependencies.py`, `app/exceptions.py`, `app/rate_limit.py`
- Create: `tests/architecture/test_core_layout.py`
- Create: `tests/architecture/test_http_contract.py`

**Interfaces:**
- Produces: `app.core.config.Settings`, `app.core.config.settings`.
- Produces: `app.core.database.Base`, `AsyncSessionLocal`, `engine`, `get_db()`.
- Produces: the existing password, token, current-user, and role helpers from `app.core.security` with unchanged signatures.
- Produces: `app.core.exceptions.register_exception_handlers(app)` and existing exception classes.
- Produces: `app.core.rate_limit.rate_limit(times, seconds, identifier=None)`.
- Preserves: `app.core.client_ip.get_client_ip(request, trust_proxy_headers=None) -> str`.

- [ ] **Step 1: Add a failing core-layout contract**

Create `tests/architecture/test_core_layout.py`:

```python
from importlib import import_module
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_core_modules_own_shared_infrastructure():
    expected = {
        "config": {"Settings", "settings"},
        "database": {"Base", "AsyncSessionLocal", "engine", "get_db"},
        "security": {
            "verify_password",
            "get_password_hash",
            "create_access_token",
            "decode_token",
            "get_current_user",
            "require_role",
            "require_admin",
            "require_staff",
        },
        "exceptions": {"APIError", "register_exception_handlers"},
        "rate_limit": {"rate_limit"},
    }
    for module_name, names in expected.items():
        module = import_module(f"app.core.{module_name}")
        assert names <= set(dir(module))


def test_flat_core_modules_are_removed():
    for name in ("auth.py", "config.py", "database.py", "dependencies.py", "exceptions.py", "rate_limit.py"):
        assert not (ROOT / "app" / name).exists()
```

- [ ] **Step 2: Add a green baseline HTTP contract before moving imports**

Create `tests/architecture/test_http_contract.py` with the exact non-documentation route set:

```python
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
```

- [ ] **Step 3: Verify the core-layout test is red and the route snapshot is green**

Run:

```bash
uv run pytest -q tests/architecture/test_core_layout.py tests/architecture/test_http_contract.py
```

Expected: core imports/removal fail; the route contract passes.

- [ ] **Step 4: Move modules without changing implementation bodies**

Move the existing implementations to their produced module paths, change imports to `app.core.*`, and update `alembic/env.py`, `scripts/check_config.py`, `app/main.py`, all current routers/services, and tests. `app/core/security.py` imports `get_db` from `app.core.database` and `User` from `app.models`; `app/core/database.py` imports `settings` from `app.core.config`; `app/core/client_ip.py` imports `settings` from `app.core.config`.

Delete the six flat files only after `rg -n "app\.(auth|config|database|dependencies|exceptions|rate_limit)" app tests alembic scripts` returns no imports.

- [ ] **Step 5: Verify core and HTTP contracts**

Run:

```bash
uv run pytest -q tests/architecture/test_core_layout.py tests/architecture/test_http_contract.py tests/test_config.py tests/test_auth.py tests/test_rate_limit.py tests/test_client_ip.py tests/test_main.py
uv run pytest -q
git diff --check
```

Expected: focused and full suites pass; no legacy core imports or whitespace errors.

- [ ] **Step 6: Commit**

```bash
git add app alembic scripts tests
git commit -m "refactor: move shared infrastructure into core"
```

---

### Task 2: Split ORM Models into a Re-exporting Package

**Files:**
- Create: `app/models/__init__.py`
- Create: `app/models/base.py`
- Create: `app/models/user.py`
- Create: `app/models/domain.py`
- Create: `app/models/short_link.py`
- Create: `app/models/access_rule.py`
- Create: `app/models/blacklist.py`
- Create: `app/models/access_log.py`
- Delete: `app/models.py`
- Create: `tests/architecture/test_model_layout.py`

**Interfaces:**
- `app.models` continues to export `User`, `Domain`, `UserDomain`, `ShortLink`, `ShortLinkPermission`, `TargetUrl`, `AccessRule`, `IpBlacklist`, and `AccessLog`.
- `app.models.base.now_utc() -> datetime` remains the default callable used by the mapped columns.
- Alembic continues to load every mapped class by importing `app.models`.

- [ ] **Step 1: Write a failing model-package contract**

```python
from importlib import import_module

from app.core.database import Base
from app.models import (
    AccessLog,
    AccessRule,
    Domain,
    IpBlacklist,
    ShortLink,
    ShortLinkPermission,
    TargetUrl,
    User,
    UserDomain,
)


def test_models_live_in_focused_modules_and_are_reexported():
    expected = {
        "user": User,
        "domain": Domain,
        "short_link": ShortLink,
        "access_rule": AccessRule,
        "blacklist": IpBlacklist,
        "access_log": AccessLog,
    }
    for module_name, model in expected.items():
        assert getattr(import_module(f"app.models.{module_name}"), model.__name__) is model


def test_table_names_are_unchanged():
    assert set(Base.metadata.tables) == {
        "users",
        "domains",
        "user_domains",
        "short_links",
        "short_link_permissions",
        "target_urls",
        "access_rules",
        "ip_blacklist",
        "access_logs",
    }
    assert ShortLinkPermission.__tablename__ == "short_link_permissions"
    assert TargetUrl.__tablename__ == "target_urls"
    assert UserDomain.__tablename__ == "user_domains"
```

- [ ] **Step 2: Run the contract red**

Run `uv run pytest -q tests/architecture/test_model_layout.py`.

Expected: `app.models.<module>` imports fail because `app/models.py` is still flat.

- [ ] **Step 3: Split declarations with identical SQLAlchemy definitions**

Put `now_utc` in `base.py`; group `User`; `Domain`/`UserDomain`; `ShortLink`/`ShortLinkPermission`/`TargetUrl`; `AccessRule`; `IpBlacklist`; and `AccessLog` in the files shown above. Use `app.core.database.Base`. Keep every `Column`, type, nullable flag, default, `onupdate`, foreign key, `ondelete`, relationship, cascade, lazy option, comment, and named constraint unchanged. Import and re-export every class from `app/models/__init__.py` so current feature imports remain valid.

- [ ] **Step 4: Verify metadata and migrations see the same models**

Run:

```bash
uv run pytest -q tests/architecture/test_model_layout.py tests/test_api.py tests/test_redirect.py tests/test_logs_extended.py
uv run pytest -q
uv run alembic heads
git diff --check
```

Expected: tests pass, Alembic reports the same repository head(s), and no migration file changes.

- [ ] **Step 5: Commit**

```bash
git add app/models tests/architecture/test_model_layout.py
git add -u app/models.py
git commit -m "refactor: split orm models by domain"
```

---

### Task 3: Move Authentication and User Administration into Features

**Files:**
- Create: `app/features/__init__.py`
- Create: `app/features/auth/{__init__.py,router.py,schemas.py,service.py}`
- Create: `app/features/users/{__init__.py,router.py,schemas.py,service.py}`
- Modify: `app/main.py`
- Modify: callers of the moved schemas
- Delete: `app/routers/auth.py`, `app/routers/admin.py`
- Create: `tests/architecture/test_auth_users_layout.py`

**Interfaces:**
- `app.features.auth.router.router` keeps prefix `/api/auth` and tag `auth`.
- `app.features.auth.schemas.Token` retains `access_token` and default `token_type="bearer"`.
- `app.features.auth.service.authenticate_user(db, username, password) -> User` raises the same unauthorized/disabled errors as the existing routes.
- `app.features.auth.service.issue_token(user) -> str` delegates to `app.core.security.create_access_token` with the unchanged subject/role payload.
- `app.features.users.router.router` keeps prefix `/api/admin/users`.
- `app.features.users.schemas` owns `UserBase`, `UserCreate`, and `UserResponse` unchanged.
- `app.features.users.service` owns list/create/update/delete workflows and current commit/refresh behavior.

- [ ] **Step 1: Add failing feature-layout tests**

```python
from app.features.auth.router import router as auth_router
from app.features.auth.schemas import Token
from app.features.auth.service import authenticate_user, issue_token
from app.features.users.router import router as users_router
from app.features.users.schemas import UserCreate, UserResponse


def test_auth_and_users_feature_interfaces():
    assert auth_router.prefix == "/api/auth"
    assert users_router.prefix == "/api/admin/users"
    assert Token(access_token="x").token_type == "bearer"
    assert callable(authenticate_user)
    assert callable(issue_token)
    assert UserCreate.model_fields["username"].is_required()
    assert UserResponse.model_config["from_attributes"] is True
```

- [ ] **Step 2: Verify red**

Run `uv run pytest -q tests/architecture/test_auth_users_layout.py tests/architecture/test_http_contract.py`.

Expected: new feature imports fail; current route contract still passes.

- [ ] **Step 3: Move schemas, routes, and workflows**

Move the auth router's `_issue_token`/credential lookup into `auth/service.py`; keep form/cookie/header extraction and `Response.set_cookie` in `auth/router.py`. Move admin user queries, uniqueness checks, password hashing, domain assignment, transaction calls, and deletes into `users/service.py`; keep FastAPI dependencies and response declarations in `users/router.py`. Use schemas from each feature and core security/database/exceptions imports. Update `app/main.py` to include `auth.router` and `users.router` in the same order and with the same rate-limit dependencies.

- [ ] **Step 4: Verify HTTP and authorization behavior**

Run:

```bash
uv run pytest -q tests/architecture/test_auth_users_layout.py tests/architecture/test_http_contract.py tests/test_auth.py tests/test_api.py -k "login or me or user"
uv run pytest -q
git diff --check
```

- [ ] **Step 5: Commit**

```bash
git add app/features app/main.py app/schemas.py tests
git add -u app/routers/auth.py app/routers/admin.py
git commit -m "refactor: package auth and user features"
```

---

### Task 4: Move Domain and Blacklist Features

**Files:**
- Create: `app/features/domains/{__init__.py,dependencies.py,router.py,schemas.py,service.py}`
- Create: `app/features/blacklist/{__init__.py,router.py,schemas.py,service.py}`
- Modify: current short-link, log, and redirect imports of domain helpers
- Modify: `app/main.py`
- Delete: `app/domains.py`, `app/routers/domains.py`, `app/routers/ip_blacklist.py`
- Create: `tests/architecture/test_domains_blacklist_layout.py`

**Interfaces:**
- `app.features.domains.dependencies.get_request_host(request) -> str` reads only `Host` and preserves current normalization.
- `get_current_domain(...)`, `require_domain_access(...)`, and domain-access checks move to `features.domains.dependencies` with unchanged FastAPI dependency behavior.
- `features.domains.service` owns list/create/get/update/delete and default-domain transaction logic.
- `features.blacklist.service` owns list/add/remove queries and commits.
- Existing prefixes `/api/domains` and `/api/ip-blacklist` are unchanged.

- [ ] **Step 1: Add a failing package and spoofing-boundary contract**

```python
from app.features.blacklist.router import router as blacklist_router
from app.features.domains.dependencies import get_request_host
from app.features.domains.router import router as domains_router
from app.features.domains.schemas import DomainCreate, DomainResponse


def test_domain_and_blacklist_feature_interfaces():
    assert domains_router.prefix == "/api/domains"
    assert blacklist_router.prefix == "/api/ip-blacklist"
    assert callable(get_request_host)
    assert DomainCreate.model_fields["name"].is_required()
    assert DomainResponse.model_config["from_attributes"] is True
```

- [ ] **Step 2: Verify red, then move exact behavior**

Run `uv run pytest -q tests/architecture/test_domains_blacklist_layout.py tests/test_domains.py` and confirm feature imports fail while spoofed-forwarded-host coverage remains green.

Move `app/domains.py` helpers to `features/domains/dependencies.py`, renaming `_get_host` to `get_request_host` and updating callers. Move domain/blacklist schemas unchanged. Extract database workflows into the two service modules without changing filters, role rules, uniqueness handling, default-domain updates, commits, refreshes, or errors. Keep only HTTP dependencies and response conversion in routers.

- [ ] **Step 3: Verify feature and full behavior**

```bash
uv run pytest -q tests/architecture/test_domains_blacklist_layout.py tests/architecture/test_http_contract.py tests/test_domains.py tests/test_blacklist.py tests/test_api.py -k "domain or blacklist or redirect"
uv run pytest -q
git diff --check
```

- [ ] **Step 4: Commit**

```bash
git add app/features app/main.py app/schemas.py app/routers tests
git add -u app/domains.py app/routers/domains.py app/routers/ip_blacklist.py
git commit -m "refactor: package domain and blacklist features"
```

---

### Task 5: Move the Short-Link Aggregate into One Feature

**Files:**
- Create: `app/features/short_links/{__init__.py,router.py,schemas.py,service.py,short_code.py}`
- Modify: `app/main.py`
- Delete: `app/routers/short_links.py`, `app/services/short_code.py`
- Create: `tests/architecture/test_short_links_layout.py`

**Interfaces:**
- Router prefix and all 16 short-link, target URL, rule, permission, and daily-stat routes remain unchanged.
- `schemas.py` owns the unchanged `TargetUrl*`, `AccessRule*`, `ShortLink*`, and `ShortLinkPermission*` models.
- `short_code.py` preserves `generate_short_code`, `validate_custom_alias`, and `create_unique_short_code` signatures/constants.
- `service.py` owns link visibility/management checks and all database workflows; it preserves query filters, eager loads, role/domain rules, default actions, transaction ordering, and errors.

- [ ] **Step 1: Add a failing aggregate-layout contract**

```python
from app.features.short_links.router import router
from app.features.short_links.schemas import (
    AccessRuleCreate,
    ShortLinkCreate,
    ShortLinkDetail,
    TargetUrlCreate,
)
from app.features.short_links.short_code import generate_short_code, validate_custom_alias


def test_short_link_feature_interfaces():
    assert router.prefix == "/api/short-links"
    assert ShortLinkCreate.model_fields["domain_id"].is_required()
    assert ShortLinkDetail.model_config["from_attributes"] is True
    assert AccessRuleCreate.model_fields["action"].is_required()
    assert TargetUrlCreate.model_fields["url"].is_required()
    assert len(generate_short_code()) == 6
    assert validate_custom_alias("abc") is True
```

- [ ] **Step 2: Verify red**

Run `uv run pytest -q tests/architecture/test_short_links_layout.py tests/test_short_code.py tests/architecture/test_http_contract.py`.

- [ ] **Step 3: Move schemas and short-code service unchanged**

Move the four schema families without changing field names, defaults, regexes, bounds, or `from_attributes`. Move `short_code.py` unchanged except imports from `app.models`. Update tests to import its feature path.

- [ ] **Step 4: Separate HTTP and business responsibilities**

Move `_resolve_effective_domain`, link visibility/management checks, CRUD queries, target/rule/permission workflows, daily-stat query, and transaction calls to `service.py`. Service functions accept the current `AsyncSession`, `User`, UUIDs, schema values, and pagination arguments and return the same ORM objects or statistics. Router functions retain their existing names, decorators, dependency declarations, query constraints, response models, and status behavior, and delegate once to the corresponding service workflow.

- [ ] **Step 5: Verify the complete aggregate**

```bash
uv run pytest -q tests/architecture/test_short_links_layout.py tests/architecture/test_http_contract.py tests/test_short_code.py tests/test_api.py tests/test_logs_extended.py
uv run pytest -q
git diff --check
```

- [ ] **Step 6: Commit**

```bash
git add app/features app/main.py app/schemas.py tests
git add -u app/routers/short_links.py app/services/short_code.py
git commit -m "refactor: package short link feature"
```

---

### Task 6: Move Access-Log Querying into Its Feature

**Files:**
- Create: `app/features/access_logs/{__init__.py,router.py,schemas.py,service.py}`
- Modify: `app/main.py`
- Delete: `app/routers/logs.py`
- Create: `tests/architecture/test_access_logs_layout.py`

**Interfaces:**
- `router` keeps `/api/logs`, `/api/logs/daily`, and `/api/logs/daily-summary` with unchanged parameters, response models, access control, ordering, offset pagination, and result shapes.
- `schemas.py` owns unchanged `AccessLogResponse` and `DailyStatsResponse`.
- `service.py` owns effective-domain resolution, link visibility, list query, daily stats, and daily summary query construction.

- [ ] **Step 1: Add a failing feature contract**

```python
from app.features.access_logs.router import router
from app.features.access_logs.schemas import AccessLogResponse, DailyStatsResponse
from app.features.access_logs.service import can_view_link, resolve_effective_domain


def test_access_log_feature_interfaces():
    assert router.prefix == "/api/logs"
    assert AccessLogResponse.model_config["from_attributes"] is True
    assert DailyStatsResponse.model_fields["unique_ips"].is_required()
    assert callable(can_view_link)
    assert callable(resolve_effective_domain)
```

- [ ] **Step 2: Verify red, move, and verify green**

Run the contract and confirm imports fail. Move schemas and query/workflow helpers to the produced modules; preserve every authorization branch, date filter, sort, offset, limit, aggregate expression, and response value. Keep FastAPI query declarations and response conversion in the router.

Run:

```bash
uv run pytest -q tests/architecture/test_access_logs_layout.py tests/architecture/test_http_contract.py tests/test_logs_extended.py tests/test_api.py -k "log or daily"
uv run pytest -q
git diff --check
```

- [ ] **Step 3: Commit**

```bash
git add app/features app/main.py app/schemas.py tests
git add -u app/routers/logs.py
git commit -m "refactor: package access log feature"
```

---

### Task 7: Move Redirect Workflows and External Adapters

**Files:**
- Create: `app/features/redirect/{__init__.py,router.py,service.py,ua.py}`
- Create: `app/integrations/__init__.py`
- Create: `app/integrations/redis.py`
- Create: `app/integrations/maxmind/{__init__.py,country.py}`
- Modify: `app/main.py`
- Delete: `app/routers/redirect.py`, `app/services/redirect.py`, `app/services/ua.py`, `app/services/geoip.py`
- Create: `tests/architecture/test_redirect_integrations_layout.py`

**Interfaces:**
- Redirect remains `GET|HEAD /{short_code}` with unchanged redirect/error/log behavior.
- `features.redirect.ua.get_platform(ua_string) -> str | None` is unchanged.
- `features.redirect.service` owns blacklist lookup, rule matching/evaluation, weighted target choice, target selection, access-log persistence, and domain/link workflow.
- `integrations.maxmind.country.get_geoip_reader()` and `get_country(ip)` preserve local GeoLite2 behavior.
- `integrations.redis.create_redis_client(url)` returns the same decoded async Redis client; `close_redis_client(client)` closes it once.

- [ ] **Step 1: Add failing adapter and redirect contracts**

```python
from app.features.redirect.router import router
from app.features.redirect.service import evaluate_rules, weighted_random_choice
from app.features.redirect.ua import get_platform
from app.integrations.maxmind.country import get_country
from app.integrations.redis import close_redis_client, create_redis_client


def test_redirect_and_integration_interfaces():
    assert router.prefix == ""
    assert callable(evaluate_rules)
    assert callable(weighted_random_choice)
    assert callable(get_platform)
    assert callable(get_country)
    assert callable(create_redis_client)
    assert callable(close_redis_client)
```

- [ ] **Step 2: Verify red**

Run `uv run pytest -q tests/architecture/test_redirect_integrations_layout.py tests/test_redirect.py tests/test_services_edge.py`.

- [ ] **Step 3: Move local MaxMind and UA code unchanged**

Move the local database reader and country lookup to `integrations/maxmind/country.py`, updating only the settings import. Move UA classification to `features/redirect/ua.py` without changing Bot precedence or return values.

- [ ] **Step 4: Move redirect rule and request workflow**

Consolidate the existing pure rule functions and redirect database helpers in `features/redirect/service.py`; keep the route decorator, request/header extraction, dependency injection, and `RedirectResponse` construction in `router.py`. Preserve the same client-IP helper, country lookup, blacklist query, Bot-as-proxy policy, access decision, URL selection, deduplication, log fields, commit behavior, and exceptions.

- [ ] **Step 5: Encapsulate Redis construction without changing lifecycle**

Implement:

```python
from redis.asyncio import Redis


def create_redis_client(url: str) -> Redis:
    return Redis.from_url(url, encoding="utf-8", decode_responses=True)


async def close_redis_client(client: Redis) -> None:
    await client.aclose()
```

Use these functions from `app/main.py`; continue storing the client at `app.state.redis`, initializing FastAPILimiter with the same client, and clearing state on shutdown. Update lifecycle tests to patch the integration functions.

- [ ] **Step 6: Verify redirect, lifecycle, and full behavior**

```bash
uv run pytest -q tests/architecture/test_redirect_integrations_layout.py tests/architecture/test_http_contract.py tests/test_redirect.py tests/test_services_edge.py tests/test_client_ip.py tests/test_main.py tests/test_api.py -k "redirect or bot or geoip or redis or health"
uv run pytest -q
git diff --check
```

- [ ] **Step 7: Commit**

```bash
git add app/features app/integrations app/main.py tests
git add -u app/routers/redirect.py app/services/redirect.py app/services/ua.py app/services/geoip.py
git commit -m "refactor: package redirect and integrations"
```

---

### Task 8: Mirror Tests, Remove Legacy Layout, and Document Boundaries

**Files:**
- Move: core tests to `tests/core/`
- Move: feature tests to matching `tests/features/<feature>/`
- Move: GeoIP tests to `tests/integrations/`
- Move: deployment tests to `tests/deployment/`
- Keep: `tests/conftest.py`, `tests/architecture/`
- Delete: `app/schemas.py`, remaining tracked files under `app/routers/` and `app/services/`
- Create: `tests/architecture/test_no_legacy_layout.py`
- Create: `docs/architecture.md`

**Interfaces:**
- Pytest collection remains automatic with root `tests/conftest.py` fixtures available to nested tests.
- Production code imports only `app.core`, `app.models`, `app.features`, and `app.integrations` boundaries.

- [ ] **Step 1: Add a failing legacy-layout audit**

```python
import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
LEGACY_FILES = {
    "auth.py",
    "config.py",
    "database.py",
    "dependencies.py",
    "domains.py",
    "exceptions.py",
    "models.py",
    "rate_limit.py",
    "schemas.py",
}
LEGACY_IMPORT_PREFIXES = ("app.routers", "app.services", "app.schemas")


def test_legacy_application_layout_is_removed():
    assert not ({path.name for path in (ROOT / "app").glob("*.py")} & LEGACY_FILES)
    assert not list((ROOT / "app" / "routers").glob("*.py"))
    assert not list((ROOT / "app" / "services").glob("*.py"))


def test_production_python_has_no_legacy_imports():
    violations = []
    for path in [*(ROOT / "app").rglob("*.py"), *(ROOT / "alembic").rglob("*.py"), *(ROOT / "scripts").rglob("*.py")]:
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            module = node.module if isinstance(node, ast.ImportFrom) else None
            names = [alias.name for alias in node.names] if isinstance(node, ast.Import) else []
            if module and module.startswith(LEGACY_IMPORT_PREFIXES):
                violations.append(f"{path}:{node.lineno}:{module}")
            violations.extend(
                f"{path}:{node.lineno}:{name}"
                for name in names
                if name.startswith(LEGACY_IMPORT_PREFIXES)
            )
    assert violations == []
```

- [ ] **Step 2: Verify the audit is red**

Run `uv run pytest -q tests/architecture/test_no_legacy_layout.py`.

Expected: `app/schemas.py` and legacy package directories still exist.

- [ ] **Step 3: Move tests into the mirrored tree**

Use these ownership rules without altering test bodies except imports:

- `test_config`, `test_client_ip`, `test_rate_limit`, `test_auth` security primitives, and `test_main` -> `tests/core/`.
- auth/user API cases -> `tests/features/auth/` and `tests/features/users/`; shared end-to-end `test_api.py` may remain `tests/features/test_api.py` as one integration suite.
- domain, blacklist, short-code, redirect, log tests -> their matching `tests/features/<name>/` packages.
- GeoIP-specific tests -> `tests/integrations/`.
- deployment config/docs -> `tests/deployment/`.

Create `__init__.py` only where relative test helpers require it; keep all fixtures in root `tests/conftest.py`.

- [ ] **Step 4: Remove legacy files and add architecture documentation**

Delete `app/schemas.py` after `rg -n "app\.schemas" app tests alembic scripts` returns no results. Remove the now-empty tracked `app/routers/__init__.py` and `app/services` files. Write `docs/architecture.md` containing the final tree above and these binding rules: routers translate HTTP only; services own workflows/transactions; models are centralized; schemas live with features; repeated complex queries may use a future `queries.py`; integrations own external systems.

- [ ] **Step 5: Run phase completion verification**

```bash
uv run pytest -q tests/architecture
uv run pytest -q
.venv/bin/python -m compileall -q app tests scripts alembic
uv run alembic heads
docker compose --env-file .env.example config --quiet
git diff --check
git status --short
```

Expected: all tests pass, Python compiles, Alembic and Compose render, only intended Task 8 changes are present, and the legacy-layout audit passes.

- [ ] **Step 6: Commit**

```bash
git add app tests docs/architecture.md
git commit -m "refactor: finalize feature-oriented project layout"
```

---

## Phase 2 Completion Gate

Phase 2 is complete only when:

- The HTTP route-contract test exactly matches the pre-refactor route set.
- The full behavioral suite passes without skipped tests added by this phase.
- SQLAlchemy exposes the same nine mapped table names and no Alembic revision changed.
- Shared infrastructure exists only under `app/core`.
- ORM declarations exist only under `app/models` and remain re-exported by `app.models`.
- Feature routers, schemas, and services live under `app/features`.
- Redis and local MaxMind adapters live under `app/integrations`.
- Production code contains no imports from the legacy router/service/schema paths.
- The test tree mirrors the production ownership boundaries.
- `docs/architecture.md` documents the final tree and dependency rules.
