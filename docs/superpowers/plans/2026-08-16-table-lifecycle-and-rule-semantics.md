# Table Lifecycle and Rule Semantics Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bring the existing nine application tables to the production data contract, replace destructive management behavior with explicit lifecycle state, and make redirect decisions explainable without implementing the Phase 5 log-search API, Phase 6 MaxMind client, or Phase 7 audit-event table.

**Architecture:** Apply five vertical, append-only Alembic revisions after `d6e8f0a21b35`, with each revision migrating one coherent domain and updating its ORM, API, service, and tests in the same task. Each migration validates legacy rows before type or uniqueness changes, backfills deterministically, adds named constraints and indexes, and only then removes superseded columns. The final task updates schema preflight and migration-path tests to treat the Phase 3 schema as repairable input and the Phase 4 schema as the required postflight contract.

**Tech Stack:** Python 3.11, FastAPI 0.111, Pydantic 2.7, SQLAlchemy 2.0 async ORM, Alembic 1.13, PostgreSQL 15, psycopg 3, pytest 8.

## Global Constraints

- Do not edit, rename, squash, or delete any migration at or before `d6e8f0a21b35`.
- Keep one linear revision chain: `d6e8f0a21b35 -> f31a8c0d4e72 -> 9b6d2f4a7c11 -> de4c81b75920 -> e52d9a6c8031 -> a73f0b9d4216`.
- Use Python `StrEnum` plus named PostgreSQL `CHECK` constraints; do not add PostgreSQL ENUM types.
- UUID, timestamp, Boolean, and JSON defaults must exist in both ORM metadata and the database through `server_default`.
- Store timestamps as timezone-aware UTC values. Compute access dates with the owning domain's IANA timezone.
- Use PostgreSQL `INET` for visitor and blacklist addresses. Validate legacy text with Python `ipaddress`; PostgreSQL 15 does not provide `pg_input_is_valid()`.
- Every foreign key must state its delete action explicitly. Access and management history must never be cascade-deleted.
- Historical actor or request facts that cannot be reconstructed remain nullable or use the explicit `legacy_unknown` decision reason; never invent an actor or a rule match.
- Existing `default_action` values remain unchanged. Only ORM, database, and new-create defaults become `deny`.
- Existing target rows with `weight < 1` migrate to `weight=1, is_active=false`; zero weight is not a supported disabled-state encoding afterward.
- Existing rules map exactly: `allow_bot=true -> any`, `allow_bot=false -> human`, `allow_proxy=true -> any`, `allow_proxy=false -> non_proxy`.
- Soft-deleted short links keep their `(domain_id, short_code)` reservation and are excluded from redirect and management queries.
- Run all PostgreSQL tests serially. Every destructive migration test must use `migration_database_url`; never run Alembic against `shorturl_test`.
- Keep routers limited to HTTP extraction/dependencies/response conversion; services own business workflow and transaction boundaries.
- Phase 5 owns keyword/result/country/time combinations and cursor pagination. Phase 6 owns Insights, Redis, circuit breaking, and final unknown-proxy fail-open orchestration. Phase 7 owns `audit_events` and complete operations documentation.

---

### Task 1: User, Domain, and Domain-Grant Lifecycle

**Files:**
- Create: `app/models/enums.py`
- Modify: `app/models/user.py`
- Modify: `app/models/domain.py`
- Modify: `app/models/__init__.py`
- Modify: `app/features/auth/service.py`
- Modify: `app/features/users/router.py`
- Modify: `app/features/users/schemas.py`
- Modify: `app/features/users/service.py`
- Modify: `app/features/domains/dependencies.py`
- Modify: `app/features/domains/router.py`
- Modify: `app/features/domains/schemas.py`
- Modify: `app/features/domains/service.py`
- Modify: `scripts/create_admin.py`
- Modify: `scripts/create_domain.py`
- Create: `alembic/versions/f31a8c0d4e72_user_domain_lifecycle.py`
- Create: `tests/migrations/test_phase4_user_domain.py`
- Modify: `tests/features/test_api.py`

**Interfaces:**
- Produces `UserRole(StrEnum)` with `admin`, `operator`, and `client` values.
- Produces `normalize_username(value: str) -> str`, `normalize_domain_name(value: str) -> str`, and `validate_timezone(value: str) -> str` in the relevant schema modules.
- Produces `Domain.timezone: str`, `Domain.updated_at: datetime`, and `UserDomain.granted_by: UUID | None`.
- Changes DELETE user/domain endpoints to state changes: users become inactive; domains become inactive and non-default.
- New `UserDomain` rows created through the API always record the authenticated administrator in `granted_by`; historical rows stay null.

- [ ] **Step 1: Write failing normalization, lifecycle, and database-contract tests**

Add API tests with literal expectations:

```python
async def test_usernames_are_lowercase_and_case_insensitively_unique(client, admin_token):
    created = await client.post(
        "/api/admin/users",
        headers=_auth(admin_token),
        json={"username": "Alice", "password": "secret1", "role": "client", "domain_ids": []},
    )
    assert created.status_code == 200
    assert created.json()["username"] == "alice"
    duplicate = await client.post(
        "/api/admin/users",
        headers=_auth(admin_token),
        json={"username": "ALICE", "password": "secret1", "role": "client", "domain_ids": []},
    )
    assert duplicate.status_code == 409


async def test_delete_user_deactivates_without_removing_row(client, admin_token):
    created = await client.post(
        "/api/admin/users",
        headers=_auth(admin_token),
        json={"username": "retained", "password": "secret1", "role": "client", "domain_ids": []},
    )
    user_id = created.json()["id"]
    deleted = await client.delete(f"/api/admin/users/{user_id}", headers=_auth(admin_token))
    assert deleted.status_code == 200
    users = await client.get("/api/admin/users", headers=_auth(admin_token))
    retained = next(user for user in users.json() if user["id"] == user_id)
    assert retained["is_active"] is False


async def test_domain_normalizes_host_and_validates_timezone(client, admin_token):
    response = await client.post(
        "/api/domains",
        headers=_auth(admin_token),
        json={"name": "Go.Example.COM", "timezone": "Asia/Shanghai"},
    )
    assert response.status_code == 200
    assert response.json()["name"] == "go.example.com"
    assert response.json()["timezone"] == "Asia/Shanghai"
```

Use a parametrized test to reject `https://go.example.com`, `go.example.com/path`, `go.example.com:443`, whitespace-only hosts, and invalid IANA timezones. Add a service/API assertion that DELETE preserves the domain row, sets `is_active=false`, and clears `is_default`.

In `tests/migrations/test_phase4_user_domain.py`, upgrade a disposable database to `d6e8f0a21b35`, insert mixed-case legacy rows, upgrade to `f31a8c0d4e72`, and assert normalized values, `domains.timezone`, `domains.updated_at`, nullable historical `granted_by`, named role/host checks, `idx_user_domains_domain`, and partial unique index `uq_domains_one_default`.

- [ ] **Step 2: Run red and name each detected break**

Run:

```bash
uv run pytest -q tests/migrations/test_phase4_user_domain.py
uv run pytest -q tests/features/test_api.py -k 'username or user_deactivates or domain_normalizes or domain_deactivates'
```

Expected: missing revision/columns and current hard-delete or unnormalized behavior fail. A test that errors because of fixture setup must be fixed until it fails on the intended production behavior.

- [ ] **Step 3: Add enums, ORM metadata, and the append-only revision**

Define values in `app/models/enums.py` as string enums, starting with:

```python
from enum import StrEnum


class UserRole(StrEnum):
    ADMIN = "admin"
    OPERATOR = "operator"
    CLIENT = "client"
```

In revision `f31a8c0d4e72`, with `down_revision = "d6e8f0a21b35"`:

1. Reject counts greater than zero for duplicate `lower(btrim(username))`, duplicate `lower(btrim(domains.name))`, multiple default domains, invalid domain host syntax, or unsupported current user roles. Error text may name the invariant but must not print row contents.
2. Normalize usernames with `lower(btrim(username))` and domains with `lower(btrim(name))`.
3. Add `domains.updated_at TIMESTAMPTZ NOT NULL DEFAULT now()` and `timezone VARCHAR(64) NOT NULL DEFAULT 'Asia/Shanghai'`.
4. Add nullable `user_domains.granted_by`, FK to users with `ON DELETE RESTRICT`, and `idx_user_domains_domain`.
5. Replace implicit user-domain delete actions with named `RESTRICT` foreign keys.
6. Add `ck_users_role`, `ck_users_username_lower`, `ck_domains_name_lower_host`, and the partial unique index:

```python
op.create_index(
    "uq_domains_one_default",
    "domains",
    ["is_default"],
    unique=True,
    postgresql_where=sa.text("is_default"),
)
```

7. Align UUID/timestamp/Boolean server defaults for `users`, `domains`, and `user_domains` with ORM metadata.

The downgrade removes new checks/index/column, restores prior user-domain FKs, and does not attempt to recover pre-normalization letter case.

- [ ] **Step 4: Implement lifecycle services and request schemas**

- Normalize usernames on create, update, login, and `create_admin.py` lookup.
- Split updates into `UserUpdate` with optional password, role, domain IDs, and active flag so an update does not require a replacement password.
- Pass `current_user` from user router create/update operations into the service and set `granted_by=current_user.id` for every new mapping.
- Preserve unchanged user-domain rows during update so their original `granted_by` is not lost; add/remove only the set difference.
- Implement DELETE user as `user.is_active = False` followed by commit.
- Normalize/validate domain names and `ZoneInfo` timezones before persistence and in `create_domain.py`.
- Pass the authenticated administrator to domain workflows only where actor data is written; domain rows themselves have no actor field.
- Implement DELETE domain as `is_active=False, is_default=False` and exclude inactive domains from request-domain resolution.
- Convert uniqueness `IntegrityError` cases into `ConflictError`, always rolling back first.

- [ ] **Step 5: Verify the vertical slice and commit**

Run serially:

```bash
uv run pytest -q tests/migrations/test_phase4_user_domain.py tests/features/test_api.py
uv run pytest -q
uv run alembic heads
git diff --check
```

Expected: sole head `f31a8c0d4e72`, all existing behavior updated to lifecycle semantics, and the full suite passes.

Commit:

```bash
git add app scripts alembic/versions/f31a8c0d4e72_user_domain_lifecycle.py tests
git commit -m "feat: add user and domain lifecycle controls"
```

---

### Task 2: Short-Link, Target, and Permission Lifecycle

**Files:**
- Modify: `app/models/short_link.py`
- Modify: `app/features/short_links/schemas.py`
- Modify: `app/features/short_links/service.py`
- Modify: `app/features/redirect/service.py`
- Create: `alembic/versions/9b6d2f4a7c11_short_link_target_lifecycle.py`
- Create: `tests/migrations/test_phase4_short_links.py`
- Modify: `tests/features/test_api.py`
- Modify: `tests/features/access_logs/test_logs_extended.py`
- Modify: `tests/features/redirect/test_redirect_service_edge.py`

**Interfaces:**
- Replaces public and ORM `description` with required `name: str` of 1–128 characters.
- Sets new-link `default_action` to `deny` at ORM, server, and service layers while preserving legacy row values.
- Produces `ShortLink.deleted_at/deleted_by`, `TargetUrl.name/updated_at`, and `ShortLinkPermission.granted_by`.
- DELETE short link becomes a soft delete; target and permission records keep their existing physical-delete semantics.

- [ ] **Step 1: Write failing API, soft-delete, and migration tests**

Update helpers to create links with `{"name": "integration test link"}` and assert responses no longer expose `description`. Add tests that:

- blank or over-128-character names return 422;
- new links return `default_action="deny"`;
- DELETE sets `deleted_at`, `deleted_by`, and `is_active=false` in SQL while list/get/redirect return no live link;
- an existing access log row remains after short-link deletion;
- `TargetUrl` rejects weight `0`, accepts optional name, and returns `updated_at`;
- a granted permission stores the current actor ID.

Migration tests insert one null/blank description, one valid description, one weight-zero target, and a legacy `default_action=allow`; after upgrade assert name fallback, preserved allow value, new default deny, disabled weight-zero target with weight one, named constraints, explicit FKs, and descending composite indexes.

Add a negative migration test with a nonblank description longer than 128 characters; assert upgrade fails and the revision is not recorded. Do not silently truncate operator data.

- [ ] **Step 2: Run red**

```bash
uv run pytest -q tests/migrations/test_phase4_short_links.py
uv run pytest -q tests/features/test_api.py -k 'short_link or target_url or permission'
```

Expected: old `description`, hard delete, zero weight, and missing actor fields fail.

- [ ] **Step 3: Add revision `9b6d2f4a7c11` and matching ORM metadata**

Use `down_revision = "f31a8c0d4e72"` and perform this order:

1. Add nullable `short_links.name`, populate `COALESCE(NULLIF(btrim(description), ''), short_code)`, reject values over 128, set `VARCHAR(128) NOT NULL`, then drop `description`.
2. Set `short_links.default_action` server default to `deny` without rewriting valid existing `allow` rows; add `ck_short_links_default_action`.
3. Add `deleted_at` and nullable `deleted_by` with explicit `RESTRICT` FK plus `ck_short_links_deleted_actor` (`deleted_by IS NULL OR deleted_at IS NOT NULL`).
4. Replace short-link domain/owner foreign keys with named `ON DELETE RESTRICT` constraints.
5. Add `idx_short_links_domain_created_at` and `idx_short_links_domain_owner_created_at` with descending `created_at`; remove the now-redundant `idx_short_links_domain`.
6. Add nullable `short_link_permissions.granted_by` with `RESTRICT`; make its link/user delete actions explicit.
7. Add `target_urls.name VARCHAR(128)`, `updated_at`, `ck_target_urls_type`, and `ck_target_urls_weight`.
8. Before the weight check, convert rows with `weight < 1` to `weight=1, is_active=false`.
9. Align UUID/time/Boolean defaults and preserve the already-correct target-log `SET NULL` behavior.

The downgrade recreates `description` from `name`, restores the five Phase 3 baseline indexes/old constraints, converts target columns back, and keeps any normalized/default values that cannot be reconstructed.

- [ ] **Step 4: Switch schemas and services to the final contract**

- Require `name` in `ShortLinkCreate`; expose `name` in list/detail/update.
- Add `TargetUrl.name` and enforce `weight >= 1` in Pydantic.
- Add `granted_by` to permission responses and write `current_user.id` during grant.
- Make every live short-link query include `ShortLink.deleted_at.is_(None)`; log authorization may still load a deleted link to prove historic ownership.
- Soft delete with one transaction:

```python
link.is_active = False
link.deleted_at = datetime.now(timezone.utc)
link.deleted_by = current_user.id
await db.commit()
```

- Remove the zero-total random fallback. `weighted_random_choice` only receives active, positive-weight targets and returns `None` for an empty set.
- Keep physical target deletion; Task 5 will prove the access-log snapshot survives it.

- [ ] **Step 5: Verify and commit**

```bash
uv run pytest -q tests/migrations/test_phase4_short_links.py tests/features/test_api.py tests/features/access_logs/test_logs_extended.py tests/features/redirect
uv run pytest -q
uv run alembic heads
git diff --check
git add app alembic/versions/9b6d2f4a7c11_short_link_target_lifecycle.py tests
git commit -m "feat: add short-link lifecycle and target constraints"
```

Expected sole head: `9b6d2f4a7c11`.

---

### Task 3: Access-Rule Semantics and Stable Decisions

**Files:**
- Modify: `app/models/enums.py`
- Modify: `app/models/access_rule.py`
- Modify: `app/features/short_links/schemas.py`
- Modify: `app/features/short_links/service.py`
- Modify: `app/features/redirect/service.py`
- Create: `alembic/versions/de4c81b75920_access_rule_semantics.py`
- Create: `tests/migrations/test_phase4_rules.py`
- Modify: `tests/features/redirect/test_redirect.py`
- Modify: `tests/features/redirect/test_redirect_service_edge.py`
- Modify: `tests/features/test_api.py`

**Interfaces:**
- Produces `AccessAction`, `ClientRequirement`, `ProxyRequirement`, `RedirectResult`, and `DecisionReason` string enums.
- Produces `RedirectDecision(result, reason, matched_rule, target)` as an immutable dataclass in `app/features/redirect/service.py`.
- Replaces `allow_bot`, `allow_proxy`, and `referer_pattern` with `client_requirement`, `proxy_requirement`, and `referer_patterns`.
- Rule ordering is always `(priority, id)` and `(short_link_id, priority)` is unique.

- [ ] **Step 1: Write failing rule-contract and migration tests**

Use literal rule objects to cover:

```python
def test_bot_must_satisfy_client_and_proxy_requirements():
    rule = AccessRule(
        name="humans without proxies",
        action="allow",
        priority=1,
        countries=[],
        ua_platforms=[],
        referer_patterns=[],
        client_requirement="human",
        proxy_requirement="non_proxy",
        is_active=True,
    )
    assert rule_matches(rule, None, "bot", None, True) is False
```

Add tests for bot-only, proxy-only, non-proxy, multiple referer patterns, uppercase country normalization, inactive rules, duplicate-priority 409, automatic next priority when omitted, deterministic equal-input ordering, matched-rule return, default-action return, and `no_target` reason.

Migration tests insert all four old boolean combinations, comma-separated referers, lower-case countries, and duplicate priorities. After upgrade assert exact semantic mapping, JSON arrays, stable duplicate re-numbering, non-null names/timestamps, checks, unique constraint, and `idx_access_rules_link_active_priority`.

- [ ] **Step 2: Run red**

```bash
uv run pytest -q tests/migrations/test_phase4_rules.py
uv run pytest -q tests/features/redirect tests/features/test_api.py -k 'rule or redirect'
```

Expected: old boolean fields and string referer behavior fail the new contract.

- [ ] **Step 3: Add revision `de4c81b75920`**

With `down_revision = "9b6d2f4a7c11"`:

1. Add nullable new columns and timestamps.
2. Normalize each JSON list in Python using rows read from the migration bind: countries uppercase two-letter strings, UA platforms lowercase strings, and referer patterns split on commas with whitespace/empty entries removed.
3. Map old booleans exactly as stated in Global Constraints.
4. For only those short links containing duplicate priorities, reassign all their rules to `0..n-1` ordered by `(old priority, id)`; leave conflict-free links unchanged.
5. Backfill `name` as `Rule <priority>` after re-numbering and set final nullability/defaults.
6. Add `ck_access_rules_action`, `ck_access_rules_client_requirement`, `ck_access_rules_proxy_requirement`, JSON-array checks, `uq_access_rules_link_priority`, and `idx_access_rules_link_active_priority`.
7. Drop `allow_bot`, `allow_proxy`, and `referer_pattern` only after the new columns are validated.

Downgrade must reject rows with `client_requirement=bot` or `proxy_requirement=proxy`, because the old schema cannot express them without broadening access. For representable rows, recreate old booleans, join referer patterns with commas, then remove new columns/constraints.

- [ ] **Step 4: Implement new API and decision engine**

- Require rule `name`; validate country codes and normalize JSON arrays in Pydantic.
- Make create priority optional. If absent, select `coalesce(max(priority), -1) + 1` for the link. Convert uniqueness races to rollback plus `ConflictError("该优先级已存在")`.
- Query only active rules ordered by `AccessRule.priority, AccessRule.id`.
- Match client and proxy requirements explicitly. Preserve Phase 4's existing proxy input (`platform == "bot"`) until Phase 6 replaces the proxy orchestration.
- Return a `RedirectDecision` containing the selected target and rule. Set reason to `blacklist`, `matched_rule`, `default_action`, or `no_target` without changing the existing commit-before-error behavior.
- Keep rule deletion physical; Task 5 adds `matched_rule_id SET NULL` and the immutable name snapshot.

- [ ] **Step 5: Verify and commit**

```bash
uv run pytest -q tests/migrations/test_phase4_rules.py tests/features/redirect tests/features/test_api.py
uv run pytest -q
uv run alembic heads
git diff --check
git add app alembic/versions/de4c81b75920_access_rule_semantics.py tests
git commit -m "feat: replace access-rule boolean semantics"
```

Expected sole head: `de4c81b75920`.

---

### Task 4: Blacklist Address and Removal Lifecycle

**Files:**
- Modify: `app/models/blacklist.py`
- Modify: `app/features/blacklist/router.py`
- Modify: `app/features/blacklist/schemas.py`
- Modify: `app/features/blacklist/service.py`
- Modify: `app/features/redirect/service.py`
- Create: `alembic/versions/e52d9a6c8031_blacklist_lifecycle.py`
- Create: `tests/migrations/test_phase4_blacklist.py`
- Modify: `tests/features/blacklist/test_blacklist.py`
- Modify: `tests/features/test_api.py`

**Interfaces:**
- Stores `IpBlacklist.ip` as PostgreSQL `INET` and requires a nonblank reason.
- Produces nullable `expires_at`, `removed_at`, `removed_by`, and `removal_reason`.
- DELETE marks an entry removed. Re-adding the same normalized address reactivates the same row; adding an active duplicate returns 409.
- Redirect blacklist lookup ignores expired or removed rows.

- [ ] **Step 1: Write failing lifecycle and INET tests**

Add API tests for canonical IPv6 output, equivalent IPv6 duplicate detection, required reason, future expiration, removed rows disappearing from active list, remove actor/reason state in SQL, reactivation of the same row ID, and redirect behavior for active versus expired/removed entries.

In migration tests insert valid IPv4/IPv6 text, null/blank reasons, and assert final INET type, canonical values, legacy reason backfill, checks, and `RESTRICT` actor FKs. Add separate failing upgrades for an invalid legacy IP and two textual IPv6 values that canonicalize to the same INET address; the raised error names only the invariant/count, never the addresses.

- [ ] **Step 2: Run red**

```bash
uv run pytest -q tests/migrations/test_phase4_blacklist.py
uv run pytest -q tests/features/blacklist/test_blacklist.py tests/features/test_api.py -k blacklist
```

- [ ] **Step 3: Add revision `e52d9a6c8031`**

Use `down_revision = "de4c81b75920"`. Read all legacy IP values through the migration bind, parse with `ipaddress.ip_address`, and build an in-memory canonical-to-count map before altering the column. Abort on invalid or duplicate canonical results. Then:

1. Backfill null/blank reasons to `Legacy blacklist entry`.
2. Alter `ip` to PostgreSQL `INET` using `ip::inet`.
3. Add expiration/removal fields and nullable actor FKs with `ON DELETE RESTRICT`.
4. Replace the created-by FK with an explicit named `RESTRICT` constraint.
5. Add `ck_ip_blacklist_reason` and `ck_ip_blacklist_removed_actor`.
6. Align UUID/time defaults.

Downgrade uses `host(ip)` to restore text and drops lifecycle columns after removing their constraints.

- [ ] **Step 4: Implement stateful blacklist services**

- Use Pydantic `IPvAnyAddress` at input and serialize canonical `str(value)`.
- List only `removed_at IS NULL` entries that are not expired.
- On add: conflict for active unexpired rows; otherwise reuse the row, replace reason/expiry/creator/created time, and clear removal fields.
- Pass `current_user` to remove workflow and set `removed_at`, `removed_by`, and a stable default removal reason when the endpoint has no request body.
- Filter redirect lookup with `removed_at IS NULL` and `(expires_at IS NULL OR expires_at > now())`.
- Fix the existing redirect blacklist test so it proves the blacklist branch rather than succeeding only because the link's default action is deny.

- [ ] **Step 5: Verify and commit**

```bash
uv run pytest -q tests/migrations/test_phase4_blacklist.py tests/features/blacklist tests/features/test_api.py -k 'blacklist or redirect'
uv run pytest -q
uv run alembic heads
git diff --check
git add app alembic/versions/e52d9a6c8031_blacklist_lifecycle.py tests
git commit -m "feat: add blacklist removal lifecycle"
```

Expected sole head: `e52d9a6c8031`.

---

### Task 5: Append-Only Access Logs and Decision Snapshots

**Files:**
- Modify: `app/models/enums.py`
- Modify: `app/models/access_log.py`
- Modify: `app/features/redirect/router.py`
- Modify: `app/features/redirect/service.py`
- Modify: `app/features/access_logs/schemas.py`
- Modify: `app/features/access_logs/service.py`
- Modify: `app/features/short_links/service.py`
- Create: `alembic/versions/a73f0b9d4216_access_log_audit_schema.py`
- Create: `tests/migrations/test_phase4_access_logs.py`
- Modify: `tests/features/redirect/test_redirect_service_edge.py`
- Modify: `tests/features/access_logs/test_logs_extended.py`
- Modify: `tests/features/test_api.py`

**Interfaces:**
- Replaces `ip`, `ua_string`, and `accessed_at_plus8` with `client_ip`, `user_agent`, and `access_date`.
- Produces nullable `matched_rule_id` with `SET NULL` plus immutable `matched_rule_name` and `target_url_snapshot`.
- Produces `decision_reason`, `request_host`, `request_method`, `proxy_check_status`, `is_anonymous`, `proxy_types`, and `proxy_source`.
- Access-log short-link/domain FKs use `RESTRICT`; target/rule FKs use `SET NULL`.
- New requests populate every explainability field currently knowable. Phase 6 may replace the provisional skipped/non-bot proxy facts after Insights is integrated.

- [ ] **Step 1: Write failing schema, snapshot, and timezone tests**

Add migration tests that load legacy logs for domains in `Asia/Shanghai` and `America/New_York`, upgrade, and assert `access_date` from each domain timezone, INET conversion, unchanged IDs/counts, target snapshot best-effort backfill, `legacy_unknown` for nonblocked historic decisions, `blacklist` for blocked decisions, bot assumption fields, nullable irrecoverable matched rule, final FKs/checks/types, and six exact final indexes.

Add application tests that perform real requests and assert:

- GET and HEAD store the correct request method and normalized host;
- a matched rule stores ID/name and `decision_reason=matched_rule`;
- deleting the rule sets only `matched_rule_id` null while the name remains;
- updating/deleting a target leaves the original URL snapshot unchanged;
- default and blacklist decisions have their explicit reason;
- no-target decisions are committed before the 403/404 response;
- soft deleting a short link preserves and still permits authorized historical log access;
- daily statistics use `access_date` and distinct `client_ip`.

- [ ] **Step 2: Run red**

```bash
uv run pytest -q tests/migrations/test_phase4_access_logs.py
uv run pytest -q tests/features/redirect/test_redirect_service_edge.py tests/features/access_logs/test_logs_extended.py tests/features/test_api.py -k 'log or snapshot or head_redirect'
```

- [ ] **Step 3: Add revision `a73f0b9d4216`**

Use `down_revision = "e52d9a6c8031"` and perform a validated add/backfill/contract sequence:

1. Validate every legacy `ip` with Python `ipaddress` before adding `client_ip INET`.
2. Add all new columns nullable, including `matched_rule_id` with a named `SET NULL` FK.
3. Backfill `client_ip`, `user_agent`, and `access_date = (accessed_at AT TIME ZONE domains.timezone)::date`.
4. Backfill target snapshots by joining current targets and request hosts by joining domains. Use `GET` for historical method.
5. Set blocked historic decisions to `blacklist`; use `legacy_unknown` for allowed/denied rows whose matching rule cannot be reconstructed.
6. Historic `ua_platform='bot'` rows get `proxy_check_status='assumed_bot'`, `is_anonymous=true`, `proxy_source='assumed_bot'`; other historic rows get `skipped`, null anonymity/source. Set `proxy_types='[]'::jsonb`.
7. Validate and set required nullability only for fields with honest backfills. Snapshot/matched/actor facts stay nullable when unrecoverable.
8. Replace log short-link/domain FKs with named `ON DELETE RESTRICT`; recreate target FK as named `ON DELETE SET NULL`.
9. Add named result, country, decision, request-method, proxy-status/source, and JSON-array checks.
10. Create the final indexes:

```text
idx_access_logs_domain_accessed_at       (domain_id, accessed_at DESC)
idx_access_logs_link_accessed_at         (short_link_id, accessed_at DESC)
idx_access_logs_link_access_date         (short_link_id, access_date DESC)
idx_access_logs_link_client_ip_dedup     (short_link_id, client_ip, dedup_bucket)
idx_access_logs_domain_result_accessed_at(domain_id, result, accessed_at DESC)
idx_access_logs_domain_country_accessed_at(domain_id, country, accessed_at DESC)
```

11. Drop the four Phase 3 access-log indexes and old `ip`, `ua_string`, and `accessed_at_plus8` columns after the final indexes exist.

The downgrade first rejects rows with decision/proxy values the old schema cannot safely represent, reconstructs old text/date/user-agent columns, restores Phase 3 indexes and prior FKs, then removes the final columns. It must never cascade-delete logs.

- [ ] **Step 4: Update ORM, redirect logging, responses, and statistics**

- Mirror all types/defaults/checks/FKs/index expressions in `AccessLog.__table_args__`.
- Pass `request.method` from the router into `execute_redirect`.
- Build the log from `RedirectDecision` and current request facts. Preserve the existing single commit before raising a denied/no-target error.
- Until Phase 6, record bot as `assumed_bot/true/assumed_bot`; record non-bot as `skipped/false/null`, matching the current evaluator's `is_proxy = platform == "bot"` behavior.
- Populate target/rule snapshots from objects selected for that request.
- Rename response fields and all date/distinct queries to `client_ip`, `user_agent`, and `access_date`.
- Compute new request `access_date` with `ZoneInfo(domain.timezone)`.
- Keep the existing offset pagination and limited filter set unchanged; Phase 5 replaces them.

- [ ] **Step 5: Verify and commit**

```bash
uv run pytest -q tests/migrations/test_phase4_access_logs.py tests/features/redirect tests/features/access_logs tests/features/test_api.py
uv run pytest -q
uv run alembic heads
git diff --check
git add app alembic/versions/a73f0b9d4216_access_log_audit_schema.py tests
git commit -m "feat: preserve explainable access-log history"
```

Expected sole head: `a73f0b9d4216`.

---

### Task 6: Final Schema Preflight, Migration Paths, and Phase Gate

**Files:**
- Modify: `tests/migrations/support.py`
- Modify: `tests/migrations/test_schema_baseline.py`
- Modify: `tests/migrations/test_alembic_paths.py`
- Create: `tests/migrations/test_phase4_full_path.py`
- Modify: `scripts/check_schema.py`
- Modify: `tests/deployment/test_schema_check.py`
- Modify: `tests/deployment/test_deployment_docs.py`
- Modify: `docs/deployment.md`
- Modify: `tests/architecture/test_model_layout.py`

**Interfaces:**
- Phase 3 tests keep proving the five repaired indexes at revision `d6e8f0a21b35`; they no longer mistake those transitional indexes for the final Phase 4 head.
- `pre` accepts empty, Phase 3, and partially migrated Phase 4 schemas when no same-named final object conflicts.
- `post` requires the final indexes and explicit log FK delete actions.
- `make migrate` remains preflight -> Alembic upgrade -> postflight.

- [ ] **Step 1: Write failing final-schema and full-path tests**

Extend migration support with unfiltered index metadata (ordered columns, uniqueness, sort direction/predicate), column types/defaults/nullability, named checks, and FK maps. Keep every engine in `try/finally: engine.dispose()`.

Add tests for:

1. Empty database -> `head`.
2. `d6e8f0a21b35` with representative legacy rows -> `head`, preserving primary keys and row counts.
3. Head -> each task's previous revision -> head round trips for representable data.
4. Duplicate lowercase users/domains, multiple defaults, overlong short-link name, invalid/colliding INET, and unrepresentable rule downgrade fail atomically.
5. Final types, defaults, named checks, partial unique index, ordered/DESC indexes, and every FK delete action.
6. `python -m alembic check` reports no new upgrade operations on a migrated database.

Update the Phase 3 baseline test to upgrade only through `d6e8f0a21b35` when asserting the original five indexes. Final ORM metadata tests assert the new index set instead of exact equality with the transitional set.

- [ ] **Step 2: Run red**

```bash
uv run pytest -q tests/migrations/test_phase4_full_path.py tests/deployment/test_schema_check.py
```

Expected: old preflight constants and Phase 3-final assumptions fail.

- [ ] **Step 3: Update preflight facts and final contract**

Replace the five-index-only constant with named final index definitions from Tasks 1–5. For each present final index, require explicit uniqueness and ordered columns; unknown facts fail closed. Inspect and require these postflight FK actions:

```python
EXPECTED_ONDELETE = {
    "access_logs.short_link_id": "RESTRICT",
    "access_logs.domain_id": "RESTRICT",
    "access_logs.target_url_id": "SET NULL",
    "access_logs.matched_rule_id": "SET NULL",
}
```

`pre` treats missing final objects as repairable, including a clean Phase 3 database. A present same-named object with wrong columns, uniqueness, predicate, or FK action is drift and stops migration. `post` requires all final objects. Continue emitting exactly one sanitized JSON object and never include connection values, SQL, or traceback.

- [ ] **Step 4: Correct field-name documentation and verify the real workflow**

Update the deployment verification query from `a.ip/a.ua_string` to `a.client_ip/a.user_agent` and include `request_host`, `request_method`, `decision_reason`, and proxy status. State that these request facts are now persisted. Do not add the Phase 7 audit-event or final rollback runbook here.

Run:

```bash
uv run pytest -q tests/migrations tests/deployment/test_schema_check.py tests/deployment/test_deployment_config.py tests/deployment/test_deployment_docs.py
make -n migrate
uv run pytest -q
.venv/bin/python -m compileall -q app tests scripts alembic
uv run alembic heads
docker compose --env-file .env.example config --quiet
git diff --check
```

Query `pg_database` after the suite and assert zero names matching `shorturl_migration_%`.

- [ ] **Step 5: Commit**

```bash
git add scripts/check_schema.py tests docs/deployment.md
git commit -m "test: verify phase four schema lifecycle"
```

---

## Phase 4 Completion Gate

Phase 4 is complete only when:

- Alembic has one linear head `a73f0b9d4216`; no revision at or before `d6e8f0a21b35` changed.
- Empty, Phase 3, representative legacy-data, and supported downgrade/upgrade paths pass in UUID-named disposable databases with zero residue.
- ORM and PostgreSQL agree on all types, defaults, nullability, named checks, indexes, predicates, and delete actions.
- Users/domains are normalized and deactivated rather than deleted; new grants record actors.
- Short links expose required `name`, default new rows to deny, soft delete, reserve codes, and preserve logs.
- Targets reject zero weight; rules use the new client/proxy/referer semantics with stable unique priority.
- Blacklist addresses use INET and expiry/removal state.
- Access logs are append-only, timezone-correct, and preserve decision/rule/target/request snapshots across later management changes.
- Schema preflight accepts repairable historical states, fails closed on conflicting facts, and postflight requires the final contract.
- The complete serial test suite, compileall, Alembic check/head/history, Compose rendering, Make dry run, and diff checks pass.
- No Phase 5 cursor/filter API, Phase 6 MaxMind/Redis integration, Phase 7 `audit_events`, log partitioning, BRIN, pg_trgm, or `ip_intelligence` table was introduced.
