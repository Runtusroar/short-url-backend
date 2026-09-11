# Short URL Backend Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the conflicting permission/rule/log models with a domain-scoped, data-preserving backend and fully tested API.

**Architecture:** Keep one FastAPI application and split only the large model, schema, and router modules by business responsibility. Authorization is enforced in SQL-backed helpers, short links are written as aggregates, and the redirect service returns one structured decision that is snapshotted into an immutable access log.

**Tech Stack:** Python 3.11, FastAPI 0.111, SQLAlchemy 2 async, PostgreSQL 15, Alembic 1.13, Redis, geoip2, DeviceDetector 6.4.0, pytest, HTTPX.

**Spec:** `docs/superpowers/specs/2026-09-12-short-url-admin-refactor-design.md`

## Global Constraints

- Preserve production user, domain, short-link, target URL, and access-log UUIDs and rows.
- Roles are exactly `admin` and `subaccount`; domain access is exactly `read` or `manage`.
- Administrators are global; subaccounts are restricted by backend queries, never only by UI state.
- Block reasons are exactly `ip`, `proxy`, `country`, `bot`, `platform`, `referer`, and `other`.
- Redirect evaluation order is IP blacklist, bot, proxy, country, platform, Referer, then target errors.
- MaxMind is queried only for non-bots when proxy blocking is enabled; provider failure is fail-open.
- Store all timestamps in UTC and convert only at query/presentation boundaries.
- Do not add repositories, managers, queues, microservices, full-text search, partitions, or generic RBAC.
- Do not modify the production server or production database in this plan.

---

### Task 1: Lock current behavior and replace the UA dependency

**Files:**
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Create: `app/services/user_agent.py`
- Create: `tests/test_user_agent.py`
- Modify: `tests/test_request_metadata.py`

**Interfaces:**
- Produces: `ParsedUserAgent` dataclass.
- Produces: `parse_user_agent(ua: str | None, headers: Mapping[str, str] | None = None) -> ParsedUserAgent`.
- Produces normalized `platform` values: `desktop`, `smartphone`, `tablet`, `tv`, `console`, `wearable`, `bot`, or `other`.

- [ ] **Step 1: Add failing DeviceDetector characterization tests**

```python
from app.services.user_agent import parse_user_agent


def test_parse_mobile_browser_with_model():
    parsed = parse_user_agent(
        "Mozilla/5.0 (Linux; Android 13; SM-S918B) "
        "AppleWebKit/537.36 Chrome/120.0.0.0 Mobile Safari/537.36"
    )
    assert parsed.browser == "Chrome Mobile"
    assert parsed.os == "Android"
    assert parsed.device_type == "smartphone"
    assert parsed.brand == "Samsung"
    assert parsed.model is not None
    assert parsed.is_bot is False


def test_parse_googlebot():
    parsed = parse_user_agent(
        "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"
    )
    assert parsed.is_bot is True
    assert parsed.platform == "bot"
    assert parsed.bot_name == "Googlebot"


def test_parse_missing_ua_is_stable():
    parsed = parse_user_agent(None)
    assert parsed.platform == "other"
    assert parsed.raw is None
```

- [ ] **Step 2: Run the focused tests and verify the new module is missing**

Run: `uv run pytest tests/test_user_agent.py tests/test_request_metadata.py -q`

Expected: FAIL because `app.services.user_agent` does not exist.

- [ ] **Step 3: Replace `user-agents` with DeviceDetector and regenerate locks**

Change the dependency entry to:

```toml
"device-detector==6.4.0",
```

Remove `user-agents`, then run:

```bash
uv lock
uv sync --dev
```

- [ ] **Step 4: Implement one typed adapter around DeviceDetector**

```python
from dataclasses import dataclass
from typing import Mapping

from device_detector import DeviceDetector


@dataclass(frozen=True, slots=True)
class ParsedUserAgent:
    raw: str | None
    platform: str
    browser: str | None
    browser_version: str | None
    os: str | None
    os_version: str | None
    device_type: str | None
    brand: str | None
    model: str | None
    is_bot: bool
    bot_name: str | None


def parse_user_agent(
    ua: str | None,
    headers: Mapping[str, str] | None = None,
) -> ParsedUserAgent:
    if not ua:
        return ParsedUserAgent(None, "other", None, None, None, None, None, None, None, False, None)
    detector = DeviceDetector(ua, headers=dict(headers or {})).parse()
    is_bot = detector.is_bot()
    device_type = detector.device_type() or None
    platform = "bot" if is_bot else _normalize_platform(device_type)
    return ParsedUserAgent(
        raw=ua,
        platform=platform,
        browser=detector.client_name() or None,
        browser_version=detector.client_version() or None,
        os=detector.os_name() or None,
        os_version=detector.os_version() or None,
        device_type=device_type,
        brand=detector.device_brand() or None,
        model=detector.device_model() or None,
        is_bot=is_bot,
        bot_name=(detector.bot.name() or None) if is_bot else None,
    )
```

Add a private `_normalize_platform` mapping and keep DeviceDetector calls inside this module.

- [ ] **Step 5: Run tests and commit**

Run: `uv run pytest tests/test_user_agent.py tests/test_request_metadata.py -q`

Expected: PASS.

```bash
git add pyproject.toml uv.lock app/services/user_agent.py tests/test_user_agent.py tests/test_request_metadata.py
git commit -m "feat: adopt DeviceDetector for request metadata"
```

### Task 2: Introduce the normalized models and data-preserving migration

**Files:**
- Create: `app/db/__init__.py`
- Create: `app/db/base.py`
- Create: `app/db/session.py`
- Create: `app/db/models/__init__.py`
- Create: `app/db/models/enums.py`
- Create: `app/db/models/user.py`
- Create: `app/db/models/domain.py`
- Create: `app/db/models/short_link.py`
- Create: `app/db/models/security.py`
- Create: `app/db/models/access_log.py`
- Modify: `app/database.py`
- Modify: `app/models.py`
- Modify: `alembic/env.py`
- Create: `alembic/versions/20260912_refactor_schema.py`
- Create: `app/services/policy_migration.py`
- Create: `scripts/check_policy_migration.py`
- Create: `scripts/backfill_user_agents.py`
- Create: `tests/test_models.py`
- Create: `tests/test_policy_migration.py`
- Create: `tests/test_migrations.py`
- Modify: `Makefile`

**Interfaces:**
- Produces SQLAlchemy models: `User`, `Domain`, `UserDomainAccess`, `ShortLink`, `TargetUrl`, `LinkPolicy`, `IpBlacklist`, `IpReputation`, `AccessLog`.
- Produces `convert_legacy_policy(link, rules) -> PolicyConversion` where `PolicyConversion.convertible` is false when behavior cannot be preserved.
- Produces CLI commands `make migration-check` and `make backfill-user-agents`.
- Keeps `app.models` and `app.database` as temporary re-export modules until Task 9 removes old imports.

- [ ] **Step 1: Write failing model and conversion tests**

```python
def test_domain_access_has_composite_primary_key():
    assert {column.name for column in UserDomainAccess.__table__.primary_key} == {
        "user_id", "domain_id"
    }


def test_access_log_foreign_keys_set_null():
    actions = {
        fk.ondelete
        for column in (AccessLog.short_link_id, AccessLog.domain_id, AccessLog.target_url_id)
        for fk in column.property.columns[0].foreign_keys
    }
    assert actions == {"SET NULL"}


def test_complex_legacy_rule_refuses_conversion():
    result = convert_legacy_policy(
        default_action="deny",
        rules=[legacy_rule(action="allow", countries=["CN"], ua_platforms=["mobile"])],
    )
    assert result.convertible is False
    assert "combined conditions" in result.reason
```

- [ ] **Step 2: Run focused tests and verify they fail**

Run: `uv run pytest tests/test_models.py tests/test_policy_migration.py -q`

Expected: FAIL because normalized models and conversion helpers do not exist.

- [ ] **Step 3: Implement model modules and compatibility exports**

Use string columns with `CheckConstraint` rather than PostgreSQL native enums so future additions do not require enum-type surgery. Centralize allowed values in `StrEnum` classes:

```python
class UserRole(StrEnum):
    ADMIN = "admin"
    SUBACCOUNT = "subaccount"


class AccessLevel(StrEnum):
    READ = "read"
    MANAGE = "manage"


class AccessResult(StrEnum):
    ALLOWED = "allowed"
    BLOCKED = "blocked"
    ERROR = "error"
```

`app/models.py` must only re-export the new classes during the transition. `app/database.py` must re-export `Base`, `engine`, `AsyncSessionLocal`, and `get_db` from `app.db`.

- [ ] **Step 4: Implement deterministic policy preflight**

`convert_legacy_policy` must accept active legacy rules sorted by priority, recognize only provably equivalent single-dimension patterns, and return affected IDs rather than guessing. The CLI exits `0` when all links are convertible and `1` with one line per affected short-link UUID otherwise.

Run: `uv run pytest tests/test_policy_migration.py -q`

Expected: PASS for empty rules, simple country/platform allow or block rules, proxy/bot flags, and refusal of mixed-condition or conflicting ordered rules.

- [ ] **Step 5: Write the forward migration with data assertions**

The migration must:

```python
revision = "20260912_refactor"
down_revision = "a9e56b03bf5f"
```

Create additive structures, migrate roles and grants, copy `description` to `note`, rename `denied` target types to `blocked`, backfill log snapshots, map historical results, convert valid IPs with `NULLIF`, create `SET NULL` foreign keys, then remove obsolete columns/tables only after conversion assertions.

Use server defaults while backfilling and remove temporary defaults afterward. Use named check constraints and the six log indexes from the design spec.

- [ ] **Step 6: Add fresh and legacy upgrade tests**

`tests/test_migrations.py` must create isolated PostgreSQL schemas, run Alembic to `a9e56b03bf5f`, insert representative legacy rows, upgrade to `20260912_refactor`, and assert:

```python
assert after.users == before.users
assert after.domains == before.domains
assert after.short_links == before.short_links
assert after.target_urls == before.target_urls
assert after.access_logs == before.access_logs
assert migrated_log.block_reason == "other"
assert migrated_log.short_code == "legacy-code"
assert migrated_log.ip is None  # legacy "unknown"
```

- [ ] **Step 7: Add the idempotent UA backfill and Make targets**

The script processes ordered batches, updates only rows with `ua_raw IS NOT NULL AND ua_browser IS NULL`, commits each batch, and exits successfully when rerun.

```make
migration-check:
	$(DOCKER_COMPOSE) exec app python scripts/check_policy_migration.py

backfill-user-agents:
	$(DOCKER_COMPOSE) exec app python scripts/backfill_user_agents.py --batch-size 500
```

- [ ] **Step 8: Run migration tests and commit**

Run: `uv run pytest tests/test_models.py tests/test_policy_migration.py tests/test_migrations.py -q`

Expected: PASS.

```bash
git add app/db app/database.py app/models.py app/services/policy_migration.py scripts alembic Makefile tests/test_models.py tests/test_policy_migration.py tests/test_migrations.py
git commit -m "feat: normalize schema with data-preserving migration"
```

### Task 3: Replace role checks with domain access authorization

**Files:**
- Create: `app/core/__init__.py`
- Create: `app/core/config.py`
- Create: `app/core/security.py`
- Create: `app/schemas/auth.py`
- Create: `app/api/__init__.py`
- Create: `app/api/auth.py`
- Modify: `app/auth.py`
- Modify: `app/config.py`
- Modify: `app/rate_limit.py`
- Modify: `app/dependencies.py`
- Create: `app/services/authorization.py`
- Create: `tests/test_authorization.py`
- Modify: `tests/conftest.py`

**Interfaces:**
- Produces: `async ensure_domain_access(db, user, domain_id, required: AccessLevel) -> Domain`.
- Produces: `authorized_domain_ids_query(user: User) -> Select` for list and aggregate routes.
- Produces: `require_admin(current_user) -> User`.
- Produces the final `/api/auth/login`, `/login-cookie`, `/logout`, and `/me` routes.
- Produces environment-aware cookie settings while keeping local development defaults.
- Removes ownership-based and short-link-specific authorization.

- [ ] **Step 1: Write the permission matrix tests**

```python
@pytest.mark.parametrize(
    ("role", "granted", "required", "allowed"),
    [
        ("admin", None, "manage", True),
        ("subaccount", "read", "read", True),
        ("subaccount", "read", "manage", False),
        ("subaccount", "manage", "read", True),
        ("subaccount", "manage", "manage", True),
        ("subaccount", None, "read", False),
    ],
)
async def test_domain_access_matrix(db, make_user, make_domain, role, granted, required, allowed):
    user = await make_user(role=role)
    domain = await make_domain()
    if granted is not None:
        db.add(UserDomainAccess(user_id=user.id, domain_id=domain.id, access_level=granted))
        await db.flush()

    if allowed:
        assert await ensure_domain_access(db, user, domain.id, AccessLevel(required)) == domain
    else:
        with pytest.raises(PermissionDeniedError):
            await ensure_domain_access(db, user, domain.id, AccessLevel(required))
```

In the actual test, create the user, domain, and optional grant with fixtures; call `ensure_domain_access`; assert the returned domain or `PermissionDeniedError`.

- [ ] **Step 2: Run and verify the old helpers cannot satisfy the matrix**

Run: `uv run pytest tests/test_authorization.py -q`

Expected: FAIL because `AccessLevel` and `ensure_domain_access` are missing from the current authorization path.

- [ ] **Step 3: Implement the two authorization helpers**

```python
async def ensure_domain_access(db, user, domain_id, required):
    domain = await db.scalar(select(Domain).where(Domain.id == domain_id, Domain.is_active.is_(True)))
    if domain is None:
        raise NotFoundError("域名")
    if user.role == UserRole.ADMIN:
        return domain
    grant = await db.scalar(select(UserDomainAccess).where(
        UserDomainAccess.user_id == user.id,
        UserDomainAccess.domain_id == domain_id,
    ))
    if grant is None or (required == AccessLevel.MANAGE and grant.access_level != AccessLevel.MANAGE):
        raise PermissionDeniedError("无权访问该域名")
    return domain
```

Catch malformed JWT subjects as 401 rather than allowing `UUID(value)` to raise a 500. Move the auth router and schemas into their final modules. Create the final settings module with `app_env`, `cookie_secure`, and existing database/Redis/token fields; use `cookie_secure` when setting the login cookie. Keep `app/auth.py` and `app/config.py` as temporary compatibility exports until Task 9, and update the existing rate-limit module to import settings and token helpers from their final locations.

- [ ] **Step 4: Update fixtures to create admin, read, and manage users**

Fixtures must expose `admin_token`, `read_token`, `manage_token`, `domain_a`, and `domain_b`, with each test transaction or cleanup preserving isolation.

Add an auth response test with `COOKIE_SECURE=true` and assert the login response contains `Secure`, `HttpOnly`, and `SameSite=lax` cookie attributes. Local development keeps `COOKIE_SECURE=false`.

- [ ] **Step 5: Run auth and authorization tests and commit**

Run: `uv run pytest tests/test_auth.py tests/test_authorization.py -q`

Expected: PASS.

```bash
git add app/core app/schemas/auth.py app/api/auth.py app/api/__init__.py app/auth.py app/rate_limit.py app/dependencies.py app/services/authorization.py tests/conftest.py tests/test_auth.py tests/test_authorization.py
git commit -m "feat: enforce domain-scoped subaccount access"
```

### Task 4: Build user, domain, and global blacklist APIs

**Files:**
- Create: `app/core/errors.py`
- Modify: `app/exceptions.py`
- Create: `app/schemas/common.py`
- Create: `app/schemas/user.py`
- Create: `app/schemas/domain.py`
- Create: `app/schemas/security.py`
- Create: `app/api/users.py`
- Create: `app/api/domains.py`
- Create: `app/api/security.py`
- Modify: `app/main.py`
- Create: `tests/api/test_users.py`
- Create: `tests/api/test_domains.py`
- Create: `tests/api/test_security.py`

**Interfaces:**
- Produces: `Page[T]` with `items`, `page`, `page_size`, and `total`.
- Produces user routes under `/api/users`, domain routes under `/api/domains`, and blacklist routes under `/api/security/ip-blacklist`.
- Produces `UserWrite.domain_access: list[DomainGrantWrite]` where each grant contains `domain_id` and `access_level`.

- [ ] **Step 1: Write failing route and authorization tests**

Cover list/create/update/deactivate users, list/create/update domains, and list/create/update/delete blacklist entries. Assert subaccounts receive 403 for every administrative mutation and the last active administrator receives `409 LAST_ADMIN_REQUIRED` when deactivation is attempted.

```python
response = await client.post(
    "/api/users",
    headers=auth(admin_token),
    json={
        "username": "reader",
        "password": "reader-pass",
        "role": "subaccount",
        "domain_access": [{"domain_id": str(domain_a.id), "access_level": "read"}],
    },
)
assert response.status_code == 201
assert response.json()["domain_access"][0]["access_level"] == "read"
```

- [ ] **Step 2: Run focused API tests and verify they fail**

Run: `uv run pytest tests/api/test_users.py tests/api/test_domains.py tests/api/test_security.py -q`

Expected: FAIL because the new paths and schemas do not exist.

- [ ] **Step 3: Implement common pagination and normalized validation**

```python
class Page(BaseModel, Generic[T]):
    items: list[T]
    page: int
    page_size: int
    total: int
```

Normalize domains through one validator that rejects schemes, paths, query strings, and ports. Normalize blacklist IPs with `ipaddress.ip_address` before querying or writing PostgreSQL `INET`.

- [ ] **Step 4: Implement user and domain endpoints in single transactions**

User updates replace the complete grant set only when `domain_access` is present; omitted passwords retain the current hash. Domain deletion returns 409 while short links still reference the domain; normal administration uses activation instead of cascading deletion.

- [ ] **Step 5: Implement global blacklist CRUD**

Required routes:

```text
GET    /api/security/ip-blacklist
POST   /api/security/ip-blacklist
PUT    /api/security/ip-blacklist/{entry_id}
DELETE /api/security/ip-blacklist/{entry_id}
```

Return creator username with each row. Only administrators may call these routes.

- [ ] **Step 6: Standardize the error envelope and run tests**

All handlers return:

```json
{"code":"IP_BLACKLIST_CONFLICT","message":"该 IP 已在黑名单中","details":null}
```

Validation responses use status 422. Authentication is 401, authorization is 403, missing/inaccessible objects are 404 where disclosure would be unsafe, and uniqueness conflicts are 409.

Run: `uv run pytest tests/api/test_users.py tests/api/test_domains.py tests/api/test_security.py -q`

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add app/api app/schemas app/core/errors.py app/main.py tests/api
git commit -m "feat: add scoped administration APIs"
```

### Task 5: Replace fragmented short-link writes with an aggregate API

**Files:**
- Create: `app/schemas/short_link.py`
- Create: `app/api/short_links.py`
- Modify: `app/services/short_code.py`
- Create: `tests/api/test_short_links.py`

**Interfaces:**
- Produces: `ShortLinkWrite` containing domain, alias/note/status, complete destination lists, and one `LinkPolicyWrite`.
- Produces: atomic `POST /api/short-links` and `PUT /api/short-links/{link_id}`.
- Produces: paginated `GET /api/short-links` and detailed `GET /api/short-links/{link_id}`.

- [ ] **Step 1: Write failing aggregate API tests**

```python
payload = {
    "domain_id": str(domain_a.id),
    "custom_alias": "CampaignA",
    "note": "Autumn campaign",
    "is_active": True,
    "destinations": [
        {"url": "https://example.com/ok", "type": "allowed", "weight": 1, "is_active": True},
        {"url": "https://example.com/blocked", "type": "blocked", "weight": 1, "is_active": True},
    ],
    "policy": {
        "country_mode": "allow",
        "countries": ["CN", "SG"],
        "platform_mode": "off",
        "platforms": [],
        "referer_mode": "off",
        "referer_patterns": [],
        "block_proxy": True,
        "block_bot": True,
    },
}
response = await client.post("/api/short-links", headers=auth(manage_token), json=payload)
assert response.status_code == 201
assert response.json()["note"] == "Autumn campaign"
assert response.json()["policy"]["block_proxy"] is True
```

Also test read users cannot mutate, manage users can mutate any link in the granted domain regardless of owner, and cross-domain IDs return 404.

- [ ] **Step 2: Run focused tests and verify failure**

Run: `uv run pytest tests/api/test_short_links.py -q`

Expected: FAIL because the aggregate contract is not implemented.

- [ ] **Step 3: Implement schema validation**

Require at least one active allowed destination. Normalize countries to uppercase and Referer patterns to lowercase host patterns. Reject `allow` modes with empty value lists and negative weights. Preserve custom alias case and generate lowercase random codes.

- [ ] **Step 4: Implement list/detail/create/update/delete routes**

Use one transaction for each aggregate write. Update child destinations by ID when present, create rows without IDs, and delete omitted rows only after the complete payload validates. Upsert the one-to-one policy. List filters are `domain_id`, `keyword`, `is_active`, `page`, and `page_size`.

- [ ] **Step 5: Run short-link and authorization tests**

Run: `uv run pytest tests/api/test_short_links.py tests/test_authorization.py tests/test_short_code.py -q`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add app/api/short_links.py app/schemas/short_link.py app/services/short_code.py tests/api/test_short_links.py
git commit -m "feat: make short-link updates atomic"
```

### Task 6: Implement persisted proxy intelligence and structured decisions

**Files:**
- Modify: `app/core/config.py`
- Modify: `app/config.py`
- Modify: `.env.example`
- Modify: `docker-compose.yml`
- Create: `app/services/proxy.py`
- Create: `app/services/access.py`
- Create: `tests/test_proxy.py`
- Create: `tests/test_access_decisions.py`

**Interfaces:**
- Produces: `ProxyResult(is_proxy: bool, proxy_type: str | None, source: str)`.
- Produces: `async get_proxy_result(db, ip, client, now) -> ProxyResult | None`; `None` means fail-open/unknown.
- Produces: `AccessContext` and `AccessDecision(result, block_reason, block_detail)` dataclasses.
- Produces: `evaluate_access(policy, context, blacklisted) -> AccessDecision`.

- [ ] **Step 1: Write failing proxy cache and decision-order tests**

```python
async def test_bot_skips_provider_when_proxy_blocking(provider, bot_ua, proxy_policy):
    context = AccessContext(ip="1.1.1.1", country="US", ua=bot_ua, referer=None)
    decision = await decide_access(proxy_policy, context, blacklisted=False, provider=provider)
    provider.assert_not_awaited()
    assert decision.result == "blocked"
    assert decision.block_reason == "proxy"


def test_blacklist_wins_over_other_reasons():
    decision = evaluate_access(block_all_policy(), blocked_context(), blacklisted=True)
    assert decision.block_reason == "ip"
```

Cover cached positive and negative results, expiry, timeout, insufficient balance, country allow/block, platform allow/block, Referer allow/block, and missing-target `other`.

- [ ] **Step 2: Run focused tests and verify failure**

Run: `uv run pytest tests/test_proxy.py tests/test_access_decisions.py -q`

Expected: FAIL because the service interfaces do not exist.

- [ ] **Step 3: Add explicit settings**

```python
app_timezone: str = "Asia/Shanghai"
trust_proxy_headers: bool = False
maxmind_account_id: int | None = None
maxmind_license_key: str | None = None
ip_reputation_ttl_hours: int = 168
maxmind_timeout_seconds: float = 1.5
```

Expose matching environment variables in `.env.example` and pass them through Compose. Development keeps `TRUST_PROXY_HEADERS=false`; production sets it true because the published application port is loopback-only behind Nginx. Keep `app/config.py` as a temporary compatibility export for `Settings` and `settings` until Task 9.

- [ ] **Step 4: Implement proxy lookup with dependency injection**

Read an unexpired `IpReputation` first. For a miss, call the injected async MaxMind client with the configured timeout, normalize only the proxy fields needed by policy, and persist both proxy and non-proxy successful results. Catch provider/timeout/balance errors, log them, and return `None` without writing a false reputation row.

- [ ] **Step 5: Implement deterministic access decisions**

```python
@dataclass(frozen=True, slots=True)
class AccessDecision:
    result: AccessResult
    block_reason: BlockReason | None = None
    block_detail: str | None = None
```

Each decision branch returns immediately in the documented order. Details name the concrete value, for example `IP 命中全局黑名单：恶意请求`, `国家 US 不在允许列表`, or `检测到代理：anonymous_vpn`.

- [ ] **Step 6: Run tests and commit**

Run: `uv run pytest tests/test_proxy.py tests/test_access_decisions.py -q`

Expected: PASS.

```bash
git add app/core/config.py .env.example docker-compose.yml app/services/proxy.py app/services/access.py tests/test_proxy.py tests/test_access_decisions.py
git commit -m "feat: explain proxy and policy decisions"
```

### Task 7: Integrate redirect selection and immutable access logging

**Files:**
- Create: `app/api/redirect.py`
- Create: `app/services/request_metadata.py`
- Create: `app/services/redirect.py`
- Create: `app/services/access_log.py`
- Create: `tests/api/test_redirect.py`
- Modify: `tests/test_request_metadata.py`

**Interfaces:**
- Produces: `RequestMetadata(ip, request_url, country, referer, ua)`.
- Produces: `choose_target(destinations, result, rng=random) -> TargetUrl | None`.
- Produces: `async write_access_log(session_factory, snapshot) -> None` that does not mutate the redirect decision.
- Produces GET and HEAD `/{short_code}` routes.

- [ ] **Step 1: Write end-to-end redirect tests first**

Test every reason through the HTTP route and assert status, `Location`, and stored log fields. Include unknown Host, inactive domain/link, valid forwarded headers with trust enabled, spoofed forwarded headers with trust disabled, missing allowed target, missing blocked target, and simulated log-write failure.

```python
response = await client.get(
    "/CampaignA",
    headers={"Host": "a.example", "User-Agent": googlebot},
    follow_redirects=False,
)
assert response.status_code == 302
log = await latest_log(db)
assert log.request_url == "http://a.example/CampaignA"
assert log.block_reason == "bot"
assert log.ua_bot_name == "Googlebot"
```

- [ ] **Step 2: Run focused tests and verify failure**

Run: `uv run pytest tests/api/test_redirect.py tests/test_request_metadata.py -q`

Expected: FAIL against the old route and log schema.

- [ ] **Step 3: Implement trusted request metadata extraction**

Use `X-Real-IP`, then the first `X-Forwarded-For` address only when `settings.trust_proxy_headers` is true. Otherwise use `request.client.host`. Validate with `ipaddress.ip_address`; invalid values become `None`. Use forwarded scheme/host only under the same trust setting.

- [ ] **Step 4: Implement target selection and redirect orchestration**

Select active destinations of type `allowed` or `blocked` according to the decision. Ignore zero weights when positive weights exist; if every active weight is zero, choose uniformly. GET returns 302 with a bodyless response; HEAD returns the same status and headers without a body.

- [ ] **Step 5: Isolate log persistence from the response decision**

Construct the complete snapshot before writing. Use a fresh `AsyncSessionLocal` for the log insert, catch and log `SQLAlchemyError`, rollback, and return. Do not hide programming errors outside the persistence boundary.

- [ ] **Step 6: Run redirect tests and commit**

Run: `uv run pytest tests/api/test_redirect.py tests/test_request_metadata.py tests/test_short_code.py -q`

Expected: PASS.

```bash
git add app/api/redirect.py app/services/request_metadata.py app/services/redirect.py app/services/access_log.py tests/api/test_redirect.py tests/test_request_metadata.py
git commit -m "feat: snapshot explained redirect decisions"
```

### Task 8: Add cursor log search and authorized dashboard aggregates

**Files:**
- Create: `app/schemas/log.py`
- Create: `app/schemas/dashboard.py`
- Create: `app/services/cursor.py`
- Create: `app/api/logs.py`
- Create: `app/api/dashboard.py`
- Create: `tests/test_cursor.py`
- Create: `tests/api/test_logs.py`
- Create: `tests/api/test_dashboard.py`

**Interfaces:**
- Produces opaque URL-safe cursor helpers over `(accessed_at, id)`.
- Produces `GET /api/access-logs` with `items` and `next_cursor`.
- Produces `GET /api/dashboard` with metrics, daily trend, top links, and top Referers.

- [ ] **Step 1: Write failing cursor and filter tests**

```python
first = await client.get(
    "/api/access-logs",
    headers=auth(read_token),
    params={"domain_id": domain_a.id, "page_size": 2, "result": "blocked"},
)
assert first.status_code == 200
assert len(first.json()["items"]) == 2
second = await client.get(
    "/api/access-logs",
    headers=auth(read_token),
    params={"domain_id": domain_a.id, "page_size": 2, "cursor": first.json()["next_cursor"]},
)
assert {row["id"] for row in first.json()["items"]}.isdisjoint(
    {row["id"] for row in second.json()["items"]}
)
```

Test keyword against short-code and note snapshots, time boundaries in configured timezone, country, result, reason, stable ordering for equal timestamps, malformed cursor 422, and cross-domain denial.

- [ ] **Step 2: Run focused tests and verify failure**

Run: `uv run pytest tests/test_cursor.py tests/api/test_logs.py tests/api/test_dashboard.py -q`

Expected: FAIL because the new endpoints and cursor helpers do not exist.

- [ ] **Step 3: Implement signed, URL-safe cursor helpers**

Encode an ISO UTC timestamp plus UUID as compact JSON using URL-safe base64 and an HMAC derived from `SECRET_KEY`. Reject invalid signatures or shapes with `INVALID_CURSOR`.

- [ ] **Step 4: Implement one SQL log query**

Apply authorization and filters before ordering. Cursor predicate is:

```python
or_(
    AccessLog.accessed_at < cursor.accessed_at,
    and_(AccessLog.accessed_at == cursor.accessed_at, AccessLog.id < cursor.id),
)
```

Fetch `page_size + 1`, return at most `page_size`, and derive `next_cursor` from the last returned row only when another row exists.

- [ ] **Step 5: Implement dashboard aggregates**

Use the requested authorized domain and a bounded 7- or 30-day range. Return totals, daily buckets, top five links, and top five non-empty Referers. Unique IP is `count(distinct AccessLog.ip)` and excludes null IPs.

- [ ] **Step 6: Run tests and commit**

Run: `uv run pytest tests/test_cursor.py tests/api/test_logs.py tests/api/test_dashboard.py -q`

Expected: PASS.

```bash
git add app/api/logs.py app/api/dashboard.py app/schemas/log.py app/schemas/dashboard.py app/services/cursor.py tests/test_cursor.py tests/api/test_logs.py tests/api/test_dashboard.py
git commit -m "feat: add scoped log search and dashboard"
```

### Task 9: Remove legacy code and prove every backend route

**Files:**
- Modify: `app/main.py`
- Modify: `app/api/__init__.py`
- Delete: `app/routers/`
- Delete: `app/domains.py`
- Delete: `app/services/ua.py`
- Delete: `app/rate_limit.py`
- Delete: `app/models.py` after all imports use `app.db.models`
- Delete: `app/schemas.py` after all imports use `app.schemas`
- Delete: `app/config.py` after all imports use `app.core.config`
- Delete: `app/auth.py` after all imports use `app.core.security`
- Delete: `app/exceptions.py` after all imports use `app.core.errors`
- Modify: `tests/test_main.py`
- Create: `tests/api/test_route_contract.py`
- Modify: `Makefile`

**Interfaces:**
- Produces the final route table and removes all old `/api/admin`, `/api/logs`, `/api/ip-blacklist`, `/rules`, and `/permissions` handlers.
- Produces a single `make test` command for the complete backend suite.

- [ ] **Step 1: Write a failing route-contract test**

```python
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


def test_no_legacy_api_routes(app):
    paths = {(method, route.path) for route in app.routes for method in route.methods or set()}
    assert EXPECTED <= paths
    assert not any(path.startswith("/api/admin") for _, path in paths)
    assert not any("/permissions" in path or "/rules" in path for _, path in paths)
```

- [ ] **Step 2: Run the contract test and verify legacy routes remain**

Run: `uv run pytest tests/api/test_route_contract.py -q`

Expected: FAIL because old routers are still included.

- [ ] **Step 3: Switch `main.py` to final routers and remove compatibility files**

Register auth, dashboard, domains, logs, security, short links, users, and redirect exactly once. Preserve rate limiting with authenticated-user identifiers for admin APIs and client-IP identifiers for redirects. Move the small `rate_limit` helper and its two identifiers into `app/dependencies.py`, then delete `app/rate_limit.py`. Log unexpected exceptions with traceback before returning the stable 500 envelope.

- [ ] **Step 4: Run the complete suite**

Run: `uv run pytest -q`

Expected: every backend test passes.

- [ ] **Step 5: Exercise the migrated app in containers**

Run:

```bash
docker compose build app
docker compose run --rm app alembic upgrade head
docker compose up -d app
curl --fail http://127.0.0.1:18000/health
```

Expected: build succeeds, migration reaches `20260912_refactor`, app starts, and health returns `{"status":"ok"}`.

- [ ] **Step 6: Run explicit endpoint smoke tests**

Use the pytest API suite as the authoritative route exercise:

Run: `uv run pytest tests/api -q`

Expected: every route group has successful, validation, authentication, and authorization coverage with no untested registered business route reported by `test_route_contract.py`.

- [ ] **Step 7: Commit**

```bash
git add app tests Makefile
git commit -m "refactor: remove superseded backend flows"
```
