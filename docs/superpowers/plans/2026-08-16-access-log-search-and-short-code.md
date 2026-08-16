# Access Log Search and Lowercase Short Codes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add permission-safe access-log combination filters and signed keyset pagination while canonicalizing every short code to lowercase without breaking representable existing URLs.

**Architecture:** Add one append-only PostgreSQL revision after the Phase 4 head to canonicalize short codes, install search indexes, and extend every cursor-driving log index with `id DESC`. Keep cursor encoding and filter normalization as pure feature modules, then make the access-log service issue one permission-scoped SQLAlchemy query joined to `short_links`; routers remain limited to HTTP extraction and response conversion.

**Tech Stack:** Python 3.11, FastAPI 0.111, Pydantic 2.7, SQLAlchemy 2.0 async ORM, Alembic 1.13, PostgreSQL 15 with `pg_trgm`, psycopg 3, pytest 8.

## Global Constraints

- Treat `docs/superpowers/specs/2026-08-16-access-log-search-and-short-code-design.md` as the binding product design.
- Do not edit, rename, squash, or delete any migration at or before `a73f0b9d4216`.
- Keep one linear revision chain and use exactly `c8e4f1a26b73` as this phase's sole new revision, with `down_revision = "a73f0b9d4216"`.
- Run all PostgreSQL tests serially. Migration and schema-check tests must use UUID-named disposable databases through `migration_database_url`; never run Alembic against `shorturl_test`.
- Store short codes as lowercase `[a-z0-9_-]{3,32}`. New automatic codes are exactly seven characters from `a-z0-9` and use `secrets.choice`.
- Normalize custom aliases, redirect path values, and log-search short-code prefixes with `strip().lower()` at their input boundaries.
- Before any migration write, reject invalid canonical short codes and same-domain `lower(btrim(short_code))` collisions with sanitized invariant errors.
- `GET /api/logs` sorts only by `accessed_at DESC, id DESC`, queries `limit + 1`, and never performs a total-count query.
- Cursor payloads are URL-safe, HMAC-SHA256 signed with `settings.secret_key`, bind the effective domain, current user scope, and all normalized filters except `limit`, and return `400 INVALID_CURSOR` for every invalid form.
- Different filter categories combine with AND; repeated countries/results combine with OR after sort-and-deduplicate normalization.
- `date_from` and `date_to` are inclusive `access_date` values already computed in the effective domain's IANA timezone.
- Admins see the effective domain, operators see owned links only, and clients see granted links only. Explicit inaccessible `short_link_id` remains 403; broad queries do not disclose inaccessible links.
- Keep `/api/logs/daily` and `/api/logs/daily-summary` request/response contracts unchanged.
- Do not add total counts, exports, Elasticsearch, MaxMind Insights, Redis proxy caching, or Phase 7 audit events.
- Keep routers limited to HTTP extraction/dependencies/response conversion; services own authorization, query construction, and pagination workflow.

---

### Task 1: Canonical Short Codes and PostgreSQL Search Foundation

**Files:**
- Create: `alembic/versions/c8e4f1a26b73_log_search_and_lowercase_codes.py`
- Create: `tests/migrations/test_phase5_log_search.py`
- Modify: `app/features/short_links/short_code.py`
- Modify: `app/features/short_links/schemas.py`
- Modify: `app/features/redirect/service.py`
- Modify: `app/models/short_link.py`
- Modify: `app/models/access_log.py`
- Modify: `scripts/check_schema.py`
- Modify: `tests/deployment/test_schema_check.py`
- Modify: `tests/migrations/support.py`
- Modify: `tests/migrations/final_contract.py`
- Modify: `tests/migrations/test_phase4_full_path.py`
- Modify: `tests/features/short_links/test_short_code.py`
- Modify: `tests/features/short_links/test_short_code_edge.py`
- Modify: `tests/features/test_api.py`
- Modify: `tests/architecture/test_short_links_layout.py`
- Modify: `tests/conftest.py`

**Interfaces:**
- Produces: `normalize_short_code(value: str) -> str` in `app.features.short_links.short_code`.
- Produces: `generate_short_code(length: int = 7) -> str` using `secrets.choice` over `abcdefghijklmnopqrstuvwxyz0123456789`.
- Produces: `validate_custom_alias(alias: str) -> bool` for already-normalized `[a-z0-9_-]{3,32}` values.
- Produces: revision `c8e4f1a26b73`, `ck_short_links_short_code_canonical`, `idx_short_links_name_trgm`, and `idx_short_links_domain_code_pattern`.
- Replaces four log-index definitions with exact `id DESC` suffixes while preserving their names.
- Extends schema inspection facts with PostgreSQL operator classes and installed extensions.

- [ ] **Step 1: Write migration contract tests before production changes**

Create `tests/migrations/test_phase5_log_search.py`. Upgrade each UUID disposable database to `a73f0b9d4216`, seed mixed-case values, and assert this exact successful transition:

```python
HEAD = "c8e4f1a26b73"
PREVIOUS = "a73f0b9d4216"


def test_phase5_upgrade_canonicalizes_codes_and_builds_search_contract(
    migration_database_url,
):
    run_alembic(migration_database_url, "upgrade", PREVIOUS)
    engine = create_engine(migration_database_url)
    domain_id, owner_id, link_id = uuid4(), uuid4(), uuid4()
    with engine.begin() as connection:
        _seed_user_domain(connection, owner_id, domain_id)
        connection.execute(
            text(
                "INSERT INTO short_links "
                "(id, domain_id, short_code, is_custom_alias, name, owner_id, "
                "is_active, default_action, created_at, updated_at) "
                "VALUES (:id, :domain, ' Promo7 ', true, 'August Promotion', "
                ":owner, true, 'deny', now(), now())"
            ),
            {"id": link_id, "domain": domain_id, "owner": owner_id},
        )
    engine.dispose()

    run_alembic(migration_database_url, "upgrade", HEAD)
    contract = get_schema_contract(migration_database_url)
    assert _scalar(migration_database_url, "SELECT short_code FROM short_links") == "promo7"
    assert "ck_short_links_short_code_canonical" in contract["checks"]["short_links"]
    assert contract["extensions"] >= {"pg_trgm"}
    assert contract["indexes"]["short_links"]["idx_short_links_name_trgm"] == {
        "columns": (None,),
        "unique": False,
        "sorting": {},
        "predicate": None,
        "using": "gin",
        "expressions": ("lower((name)::text)",),
        "operator_classes": ("gin_trgm_ops",),
    }
    assert contract["indexes"]["access_logs"][
        "idx_access_logs_domain_accessed_at"
    ]["columns"] == ("domain_id", "accessed_at", "id")
    assert contract["indexes"]["access_logs"][
        "idx_access_logs_domain_accessed_at"
    ]["sorting"] == {"accessed_at": ("desc",), "id": ("desc",)}
```

Use the real inspector output to lock the normalized expression spelling once observed; do not weaken equality to substring assertions. Add equivalent exact assertions for:

```text
idx_short_links_domain_code_pattern
idx_access_logs_link_accessed_at
idx_access_logs_domain_result_accessed_at
idx_access_logs_domain_country_accessed_at
```

Define `_seed_user_domain(connection, owner_id: UUID, domain_id: UUID) -> None` with parameterized `INSERT` statements for one active admin user and one active non-default domain, and `_scalar(database_url: str, statement: str) -> object` with an engine disposed in `finally`.

Also add atomic negative cases:

```python
@pytest.mark.parametrize(
    ("codes", "invariant"),
    [
        (("Promo", "promo"), "short code normalization collision invariant"),
        (("bad code!",), "short code canonicalization invariant"),
        (("ab",), "short code canonicalization invariant"),
    ],
)
def test_short_code_preflight_rejects_before_any_write(
    migration_database_url, codes, invariant
):
    run_alembic(migration_database_url, "upgrade", PREVIOUS)
    seed_links_with_codes(migration_database_url, codes)
    with pytest.raises(CalledProcessError) as exc_info:
        run_alembic(migration_database_url, "upgrade", HEAD)
    assert invariant in f"{exc_info.value.stdout}\n{exc_info.value.stderr}"
    assert current_revision(migration_database_url) == PREVIOUS
    assert original_codes(migration_database_url) == list(codes)
    assert not has_check(migration_database_url, "ck_short_links_short_code_canonical")
    assert not has_index(migration_database_url, "idx_short_links_name_trgm")
```

Define `seed_links_with_codes(database_url: str, codes: tuple[str, ...]) -> None`, `current_revision(database_url: str) -> str`, `original_codes(database_url: str) -> list[str]`, `has_check(database_url: str, name: str) -> bool`, and `has_index(database_url: str, name: str) -> bool` in the same test module. Each helper must open and dispose its own synchronous engine in `try/finally`; seed one user and one domain, then one short link per code in that same domain using UUID parameters and `text()` statements.

Add a representable downgrade test that asserts the four Phase 4 log indexes regain their old columns/sorting, the new check and two short-link indexes disappear, `pg_trgm` remains installed, lowercase data remains lowercase, and re-upgrade succeeds.

- [ ] **Step 2: Run the new migration tests and verify the intended red state**

Run serially:

```bash
uv run pytest -q tests/migrations/test_phase5_log_search.py
```

Expected: collection or upgrade fails because revision `c8e4f1a26b73` and the new contract objects do not exist. Fix fixture/setup errors until failures are about missing production behavior.

- [ ] **Step 3: Implement the append-only migration**

In `c8e4f1a26b73_log_search_and_lowercase_codes.py`, make the first call in `upgrade()` a read-only preflight. The core checks must have these semantics:

```python
invalid = connection.scalar(
    sa.text(
        "SELECT count(*) FROM short_links "
        "WHERE lower(btrim(short_code)) !~ '^[a-z0-9_-]{3,32}$'"
    )
)
if invalid:
    raise RuntimeError("short code canonicalization invariant")

collisions = connection.scalar(
    sa.text(
        "SELECT count(*) FROM ("
        "SELECT domain_id, lower(btrim(short_code)) "
        "FROM short_links GROUP BY domain_id, lower(btrim(short_code)) "
        "HAVING count(*) > 1) AS conflicts"
    )
)
if collisions:
    raise RuntimeError("short code normalization collision invariant")
```

After preflight:

1. Execute `CREATE EXTENSION IF NOT EXISTS pg_trgm`.
2. Update all codes with `lower(btrim(short_code))`.
3. Create `ck_short_links_short_code_canonical` with both exact normalization and regex checks.
4. Create `idx_short_links_name_trgm` using GIN on `lower(name) gin_trgm_ops`.
5. Create `idx_short_links_domain_code_pattern` on `domain_id, short_code varchar_pattern_ops`.
6. Drop and recreate the four named log indexes with exact `id DESC` suffixes.

`downgrade()` must drop the new check/search indexes, restore the exact Phase 4 definitions of the four log indexes, and leave `pg_trgm` and lowercase data in place.

- [ ] **Step 4: Align ORM metadata and runtime short-code behavior**

Replace the generator and validation constants in `short_code.py` with:

```python
import re
import secrets
import string

CHARSET = string.ascii_lowercase + string.digits
CODE_LENGTH = 7
CUSTOM_ALIAS_RE = re.compile(r"^[a-z0-9_-]{3,32}$")


def normalize_short_code(value: str) -> str:
    return value.strip().lower()


def generate_short_code(length: int = CODE_LENGTH) -> str:
    return "".join(secrets.choice(CHARSET) for _ in range(length))
```

`create_unique_short_code()` normalizes a supplied alias before validation, lookup, and return. `ShortLinkCreate.custom_alias` must no longer reject uppercase before normalization; use a safe before-validator that returns non-string values unchanged for Pydantic to reject and returns the normalized string for string input.

At the redirect boundary, call `normalize_short_code(short_code)` before resolving the link. Keep the database comparison exact because head data is canonical. Add API tests proving a created custom alias `" Promo_7 "` is returned as `promo_7`, `/PROMO_7` redirects to it, an automatic code matches `^[a-z0-9]{7}$`, and a same-domain differently-cased alias returns the existing conflict contract.

In ORM metadata add the named short-code check, the two search indexes, and exact `id.desc()` suffixes to the four log indexes. Use an explicitly labeled `func.lower(name)` expression so `postgresql_ops={"name_lower": "gin_trgm_ops"}` is deterministic.

The shared application-test database is built with `Base.metadata.create_all()` rather than Alembic. In `tests/conftest.py`, execute `CREATE EXTENSION IF NOT EXISTS pg_trgm` through `_sync_engine.begin()` immediately before `Base.metadata.create_all(_sync_engine)`. Do not add this shortcut to application startup; production schema ownership remains Alembic-only.

- [ ] **Step 5: Make schema preflight distinguish the Phase 4 predecessor from drift**

Update the independent literal facts in `tests/deployment/test_schema_check.py` first, then update `scripts/check_schema.py`. Postflight requires the full Phase 5 definitions, operator classes, and `pg_trgm`. Preflight accepts exactly either:

- the complete Phase 4 predecessor definitions for the four replaced log indexes, with the two search indexes/check/extension absent; or
- the complete Phase 5 definitions.

Any other same-named definition remains a fail-closed drift. Add pure and disposable PostgreSQL tests for:

```text
Phase 4 head -> pre ok/repairable
Phase 5 head -> pre ok/ready and post ok/ready
missing pg_trgm at post -> required_extension_missing
operator class unknown -> index_operator_class_unknown
wrong varchar_pattern_ops -> index_operator_class_mismatch
wrong gin_trgm_ops -> index_operator_class_mismatch
old log index plus one unexpected extra id/ASC fact -> index_definition_mismatch
```

Extend `inspect_schema()` and `tests/migrations/support.get_schema_contract()` to inspect expressions, access method, operator classes, and installed extensions from PostgreSQL catalogs when SQLAlchemy reflection does not expose a stable fact. Use bound values or quoted identifiers; never interpolate database content into executable SQL.

- [ ] **Step 6: Update the independent final contract and migration-path head**

Update `tests/migrations/final_contract.py` using only literals; it must not import application models, migrations, or `scripts.check_schema`. Add the canonical check, two short-link indexes, and exact four keyset index definitions.

Update `tests/migrations/test_phase4_full_path.py` so its final head is `c8e4f1a26b73`, its predecessor round-trip list includes `a73f0b9d4216`, and its seeded short code verifies canonicalization. Preserve the existing exact nine-table row counts/IDs and conversion assertions.

- [ ] **Step 7: Verify the foundation and commit**

Run serially:

```bash
uv run pytest -q tests/features/short_links tests/features/test_api.py -k 'short_code or custom_alias or redirect'
uv run pytest -q tests/migrations/test_phase5_log_search.py tests/migrations/test_phase4_full_path.py tests/deployment/test_schema_check.py tests/architecture/test_short_links_layout.py
uv run alembic heads
uv run python -m compileall -q app scripts tests
git diff --check
```

Expected: sole head `c8e4f1a26b73`, every selected test passes, and no migration at or before `a73f0b9d4216` changed.

Commit:

```bash
git add alembic/versions/c8e4f1a26b73_log_search_and_lowercase_codes.py app/features/short_links app/features/redirect/service.py app/models scripts/check_schema.py tests
git commit -m "feat: canonicalize short codes and log indexes"
```

---

### Task 2: Normalized Filters and Signed Cursor Codec

**Files:**
- Create: `app/features/access_logs/query.py`
- Create: `app/features/access_logs/cursor.py`
- Create: `tests/features/access_logs/test_query.py`
- Create: `tests/features/access_logs/test_cursor.py`
- Modify: `app/features/access_logs/schemas.py`

**Interfaces:**
- Produces: immutable `AccessLogFilters` with `from_values(*, short_link_id: UUID | None, short_code: str | None, name: str | None, date_from: date | None, date_to: date | None, countries: Sequence[str], results: Sequence[RedirectResult | str]) -> AccessLogFilters` and `digest_scope(effective_domain_id: UUID, current_user: User) -> str`.
- Produces: `AccessLogFilterParams(BaseModel)` with `to_filters() -> AccessLogFilters`, including cross-field date validation and ISO country validation.
- Produces: immutable `LogCursor(accessed_at: datetime, id: UUID)`.
- Produces: `encode_cursor(position: LogCursor, filter_digest: str, secret_key: str) -> str`.
- Produces: `decode_cursor(value: str, filter_digest: str, secret_key: str) -> LogCursor`.
- Produces: `InvalidCursorError(APIError)` with code `INVALID_CURSOR`, message `无效的分页游标`, and status 400.
- Produces: `AccessLogItemResponse` and `AccessLogPageResponse` without changing daily schemas.

- [ ] **Step 1: Write pure normalization and digest tests**

In `test_query.py`, use literal cases:

```python
def test_filters_trim_normalize_sort_and_deduplicate():
    filters = AccessLogFilters.from_values(
        short_link_id=None,
        short_code=" Promo_ ",
        name="  八月 推广  ",
        date_from=date(2026, 8, 1),
        date_to=date(2026, 8, 7),
        countries=["us", "CN", "US"],
        results=["denied", "allowed", "denied"],
    )
    assert filters.short_code == "promo_"
    assert filters.name == "八月 推广"
    assert filters.countries == ("CN", "US")
    assert filters.results == (RedirectResult.ALLOWED, RedirectResult.DENIED)


def test_filter_digest_binds_user_domain_and_filters_but_not_limit():
    domain_id = UUID("00000000-0000-0000-0000-000000000010")
    other_domain_id = UUID("00000000-0000-0000-0000-000000000011")
    user = SimpleNamespace(
        id=UUID("00000000-0000-0000-0000-000000000020"), role="operator"
    )
    other_user = SimpleNamespace(
        id=UUID("00000000-0000-0000-0000-000000000021"), role="operator"
    )
    filters = AccessLogFilters.from_values(
        short_link_id=None,
        short_code="promo",
        name="August",
        date_from=date(2026, 8, 1),
        date_to=date(2026, 8, 7),
        countries=["CN", "US"],
        results=["allowed", "denied"],
    )
    equivalent_filters = AccessLogFilters.from_values(
        short_link_id=None,
        short_code=" PROMO ",
        name=" August ",
        date_from=date(2026, 8, 1),
        date_to=date(2026, 8, 7),
        countries=["US", "CN", "US"],
        results=["denied", "allowed"],
    )
    changed_name = replace(filters, name="September")
    first = filters.digest_scope(domain_id, user)
    assert first == equivalent_filters.digest_scope(domain_id, user)
    assert first != changed_name.digest_scope(domain_id, user)
    assert first != filters.digest_scope(other_domain_id, user)
    assert first != filters.digest_scope(domain_id, other_user)
```

Add Pydantic request validation tests for blank/overlong name, invalid prefix characters, `date_from > date_to`, invalid or unassigned country `ZZ`, and invalid result. Use the already pinned `pycountry` package for ISO membership.

- [ ] **Step 2: Write cursor round-trip and rejection tests**

In `test_cursor.py`:

```python
POSITION = LogCursor(
    accessed_at=datetime(2026, 8, 16, 2, 30, tzinfo=timezone.utc),
    id=UUID("00000000-0000-0000-0000-000000000123"),
)


def test_signed_cursor_round_trip():
    encoded = encode_cursor(POSITION, "filters-v1", "test-secret")
    assert decode_cursor(encoded, "filters-v1", "test-secret") == POSITION


@pytest.mark.parametrize("mutation", [tamper_payload, tamper_signature, truncate])
def test_cursor_rejects_tampering(mutation):
    encoded = encode_cursor(POSITION, "filters-v1", "test-secret")
    with pytest.raises(InvalidCursorError):
        decode_cursor(mutation(encoded), "filters-v1", "test-secret")
```

Define the three mutation helpers above the parametrized test. `tamper_payload()` and `tamper_signature()` split on the single dot and replace the first Base64 character of the selected non-empty part with `"A"` unless it is already `"A"`, in which case they use `"B"`; `truncate()` returns the token without its final character. This guarantees mutation without decoding or accidentally preserving the original token.

Also reject malformed Base64, extra JSON keys, version other than `1`, naive/non-UTC time, invalid UUID, wrong secret, wrong filter digest, non-canonical payload encoding, and oversized cursor input. Error messages must never echo the cursor.

- [ ] **Step 3: Run the pure tests and verify red**

Run:

```bash
uv run pytest -q tests/features/access_logs/test_query.py tests/features/access_logs/test_cursor.py
```

Expected: imports fail because `query.py`, `cursor.py`, and their interfaces do not exist.

- [ ] **Step 4: Implement immutable normalized filters**

Implement `AccessLogFilters` as a frozen dataclass. Its canonical digest payload must be JSON with sorted keys and compact separators, then SHA-256 hashed:

```python
payload = {
    "v": 1,
    "domain_id": str(effective_domain_id),
    "user_id": str(current_user.id),
    "role": str(current_user.role),
    "short_link_id": str(self.short_link_id) if self.short_link_id else None,
    "short_code": self.short_code,
    "name": self.name,
    "date_from": self.date_from.isoformat() if self.date_from else None,
    "date_to": self.date_to.isoformat() if self.date_to else None,
    "countries": list(self.countries),
    "results": [value.value for value in self.results],
}
```

Do not include `limit` or `cursor`. Validate a normalized `country_code` through `pycountry.countries.get(alpha_2=country_code)`, not with a two-letter regex alone.

Implement `AccessLogFilterParams` in `schemas.py` with the same fields and defaults. A model-level after-validator enforces `date_from <= date_to`; field validators normalize/validate name, short-code prefix, countries, and results. `to_filters()` is the only path from validated HTTP values to the frozen `AccessLogFilters` dataclass.

- [ ] **Step 5: Implement the strict HMAC cursor codec**

Use a two-part token:

```text
base64url(canonical-json-without-padding).base64url(hmac-sha256-without-padding)
```

The payload has exactly `{"v": 1, "at": <UTC ISO-8601>, "id": <UUID>, "f": <digest>}`. Decode with `base64.b64decode(encoded_part, altchars=b"-_", validate=True)`, restore padding only after enforcing a maximum token length, compare signatures with `hmac.compare_digest`, reject unknown/extra/missing fields, and convert every parse exception to `InvalidCursorError` without including input data.

- [ ] **Step 6: Add response schemas without touching daily responses**

Define:

```python
class AccessLogItemResponse(AccessLogResponse):
    short_code: str
    short_link_name: str


class AccessLogPageResponse(BaseModel):
    items: list[AccessLogItemResponse]
    next_cursor: str | None
    has_more: bool
```

Keep `DailyStatsResponse` unchanged.

- [ ] **Step 7: Verify and commit**

Run:

```bash
uv run pytest -q tests/features/access_logs/test_query.py tests/features/access_logs/test_cursor.py
uv run python -m compileall -q app/features/access_logs
git diff --check
```

Commit:

```bash
git add app/features/access_logs tests/features/access_logs/test_query.py tests/features/access_logs/test_cursor.py
git commit -m "feat: add signed access-log cursors"
```

---

### Task 3: Permission-Safe Combination Query API

**Files:**
- Modify: `app/features/access_logs/router.py`
- Modify: `app/features/access_logs/service.py`
- Modify: `app/features/access_logs/schemas.py`
- Modify: `tests/features/access_logs/test_logs_extended.py`
- Modify: `tests/features/test_api.py`
- Modify: `tests/architecture/test_access_logs_layout.py`
- Modify: `tests/architecture/test_http_contract.py`

**Interfaces:**
- Consumes: `AccessLogFilters`, `LogCursor`, `encode_cursor()`, `decode_cursor()`, and `AccessLogPageResponse` from Task 2.
- Produces: immutable `AccessLogListRow(log: AccessLog, short_code: str, short_link_name: str)`.
- Produces: immutable `AccessLogPage(items: tuple[AccessLogListRow, ...], next_cursor: str | None, has_more: bool)`.
- Changes: `list_logs(db: AsyncSession, current_user: User, current_domain: Domain, domain_id: UUID | None, filters: AccessLogFilters, limit: int, cursor: str | None) -> AccessLogPage`.
- Preserves: `daily_stats()` and `daily_summary()` behavior and schemas.

- [ ] **Step 1: Convert existing list tests to the accepted page response**

Replace bare-list assertions only for `GET /api/logs`:

```python
payload = response.json()
assert payload["has_more"] is False
assert payload["next_cursor"] is None
assert len(payload["items"]) == 2
```

Do not change `/daily` or `/daily-summary` assertions. Add an architecture assertion that the list endpoint uses `AccessLogPageResponse` while the two daily endpoints retain list responses.

- [ ] **Step 2: Write failing combination-filter API tests**

Seed at least three links with distinct canonical codes/names and logs spanning two dates, countries, and results. Add one parameterized test whose expected log IDs are literal:

```python
@pytest.mark.parametrize(
    ("params", "expected_names"),
    [
        ({"short_code": "promo"}, {"August Promotion", "Promo Backup"}),
        ({"name": "AUGUST"}, {"August Promotion"}),
        ({"country": ["CN", "US"]}, {"August Promotion", "US Campaign"}),
        ({"result": ["denied", "blocked"]}, {"Promo Backup"}),
        ({"date_from": "2026-08-16", "date_to": "2026-08-16"}, {"August Promotion"}),
        (
            {"short_code": "promo", "country": ["CN"], "result": ["allowed"]},
            {"August Promotion"},
        ),
    ],
)
async def test_log_filters_combine_with_and_and_multivalue_or(
    client, admin_token, seeded_log_search_records, params, expected_names
):
    admin_headers = {
        "Authorization": f"Bearer {admin_token}",
        "Host": "test.local",
    }
    response = await client.get("/api/logs", params=params, headers=admin_headers)
    assert response.status_code == 200
    assert {row["short_link_name"] for row in response.json()["items"]} == expected_names
```

Define `seeded_log_search_records` as a pytest fixture in the same module. It must insert its deterministic links/logs through the real API where result generation matters and direct parameterized SQL only where an exact historical `access_date`, `country`, `result`, `accessed_at`, or UUID is required. Return the inserted IDs and names in a frozen dataclass so assertions never discover expected values by querying production code.

Send repeated values as a list of `(key, value)` tuples so the test exercises FastAPI's repeated-query parsing. Assert short-code search accepts uppercase input and remains a literal prefix, not SQL wildcard syntax.

- [ ] **Step 3: Write failing broad-query authorization tests**

Create an admin-owned link, an operator-owned link, and two permission variants. Assert:

```text
admin broad query -> both links in the effective domain
operator broad query -> only operator-owned link
client broad query -> only currently granted link
operator explicit admin link -> 403
client explicit ungranted link -> 403
broad name/code filter for inaccessible link -> 200 with empty items
soft-deleted owned/granted link -> historical rows remain visible
non-admin domain_id override -> 403
```

These must be real API/DB tests; do not mock `can_view_link()` or replace the SQL query.

- [ ] **Step 4: Write failing keyset behavior tests**

Insert deterministic access logs directly with the same `accessed_at` and ordered UUIDs. Request `limit=2`, follow every returned cursor, and assert concatenated IDs equal the one literal descending sequence with no duplicates.

After the first page, insert a newer log and then request page two with the old cursor. Assert page two starts strictly after the original page-one tail and does not contain the new log or repeat page one. Add API cases for tampered cursor, filter change, and user change returning:

```json
{"code": "INVALID_CURSOR", "message": "无效的分页游标"}
```

Assert equivalent country/result ordering and duplicates keep the cursor valid, and changing only `limit` keeps it valid.

After a client receives page one, revoke its short-link permission and assert the same valid cursor returns no inaccessible records. This proves authorization is re-evaluated rather than carried inside the cursor.

- [ ] **Step 5: Run the focused API tests and verify red**

Run serially:

```bash
uv run pytest -q tests/features/access_logs/test_logs_extended.py tests/features/test_api.py -k 'logs or cursor or log_filter or broad_query'
```

Expected: old array/offset contract, missing filter parameters, and missing broad-query authorization make the new assertions fail.

- [ ] **Step 6: Implement the permission-scoped SQL query**

Build one base statement:

```python
statement = (
    select(AccessLog, ShortLink.short_code, ShortLink.name)
    .join(ShortLink, ShortLink.id == AccessLog.short_link_id)
    .where(
        AccessLog.domain_id == effective_domain.id,
        ShortLink.domain_id == effective_domain.id,
    )
)
```

Apply role scope in SQL:

```python
if current_user.role == UserRole.OPERATOR:
    statement = statement.where(ShortLink.owner_id == current_user.id)
elif current_user.role == UserRole.CLIENT:
    statement = statement.where(
        exists().where(
            ShortLinkPermission.short_link_id == ShortLink.id,
            ShortLinkPermission.user_id == current_user.id,
        )
    )
```

Then apply normalized filters. Use `ShortLink.short_code.startswith(value, autoescape=True)`, `func.lower(ShortLink.name).contains(name.lower(), autoescape=True)`, inclusive `AccessLog.access_date`, and `IN` for tuple countries/results. For a decoded cursor use:

```python
tuple_(AccessLog.accessed_at, AccessLog.id) < tuple_(cursor.accessed_at, cursor.id)
```

Order by both descending columns, limit to `limit + 1`, construct rows from the first `limit`, and sign the last returned row only when an additional row exists.

For explicit `short_link_id`, call the existing authorization path before list execution so inaccessible IDs remain 403. For non-admin `domain_id` differing from `current_domain.id`, raise `PermissionDeniedError` instead of silently ignoring it.

- [ ] **Step 7: Wire FastAPI parameters and page conversion**

The router list endpoint must use:

```python
@router.get("", response_model=AccessLogPageResponse)
async def list_logs(
    short_link_id: UUID | None = None,
    short_code: str | None = Query(None),
    name: str | None = Query(None),
    date_from: date | None = None,
    date_to: date | None = None,
    country: list[str] = Query(default=[]),
    result: list[RedirectResult] = Query(default=[]),
    limit: int = Query(50, ge=1, le=200),
    cursor: str | None = Query(None, max_length=2048),
    domain_id: UUID | None = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_domain: Domain = Depends(require_domain_access),
):
```

Construct `AccessLogFilters` once, call the service, and convert every `AccessLogListRow` into `AccessLogItemResponse` while preserving all current audit fields. Remove `offset` entirely; a supplied `offset` must not influence results and OpenAPI must not advertise it.

Construct `AccessLogFilterParams` inside the router from the already parsed query values, then call `.to_filters()`. Let Pydantic `ValidationError` reach the existing global handler so invalid cross-field/country/name/prefix values return the existing `400 VALIDATION_ERROR` envelope.

- [ ] **Step 8: Verify API behavior and commit**

Run serially:

```bash
uv run pytest -q tests/features/access_logs tests/features/test_api.py tests/architecture/test_access_logs_layout.py tests/architecture/test_http_contract.py
uv run python -m compileall -q app/features/access_logs
git diff --check
```

Commit:

```bash
git add app/features/access_logs tests/features/access_logs tests/features/test_api.py tests/architecture
git commit -m "feat: add permission-safe log search"
```

---

### Task 4: Phase 5 Documentation and Final Verification Gate

**Files:**
- Modify: `docs/deployment.md`
- Create: `tests/deployment/test_log_search_docs.py`
- Modify: `tests/migrations/test_phase4_full_path.py`
- Modify: `tests/migrations/test_schema_baseline.py`
- Modify: `tests/migrations/final_contract.py`
- Modify: `tests/deployment/test_schema_check.py`

**Interfaces:**
- Consumes: revision `c8e4f1a26b73`, the signed cursor API, and the final literal schema contract from Tasks 1–3.
- Produces: an operator-facing query example using repeated country/result parameters and cursor continuation.
- Produces: final disposable-database upgrade, downgrade/re-upgrade, drift, and `alembic check` gates for Phase 5.

- [ ] **Step 1: Write the documentation contract test first**

Create `tests/deployment/test_log_search_docs.py` and assert `docs/deployment.md` contains:

```text
GET /api/logs
short_code
name
date_from
date_to
country=CN&country=US
result=allowed&result=denied
next_cursor
has_more
accessed_at DESC, id DESC
```

Also assert the document says short codes are canonical lowercase, request paths are case-insensitive at the application boundary, cursor values are opaque, filters must not change while following a cursor, and operator/client results are permission-scoped.

- [ ] **Step 2: Run the docs test and verify red**

Run:

```bash
uv run pytest -q tests/deployment/test_log_search_docs.py
```

Expected: fail because the deployment runbook does not yet document the Phase 5 API.

- [ ] **Step 3: Update the deployment runbook**

Add one concise section with a first-page curl and next-page curl. Use shell-safe placeholder values and never embed a real token or secret:

```bash
curl -G 'https://go.example.com/api/logs' \
  -H 'Authorization: Bearer <token>' \
  --data-urlencode 'short_code=promo' \
  --data-urlencode 'name=八月' \
  --data-urlencode 'country=CN' \
  --data-urlencode 'country=US' \
  --data-urlencode 'result=allowed' \
  --data-urlencode 'result=denied' \
  --data-urlencode 'date_from=2026-08-01' \
  --data-urlencode 'date_to=2026-08-07' \
  --data-urlencode 'limit=50'
```

Explain that `next_cursor` is passed unchanged as `cursor`, filters stay identical, no total count is returned, and sorting is `accessed_at DESC, id DESC`.

- [ ] **Step 4: Add final disposable-database path and drift gates**

Ensure the migration suite explicitly proves:

```text
empty -> c8e4f1a26b73
D6 representative rows -> c8e4f1a26b73 with exact nine-table IDs/counts
c8e4f1a26b73 -> a73f0b9d4216 -> c8e4f1a26b73
each earlier task revision -> c8e4f1a26b73
mixed-case collision failure leaves version/data/schema at a73f0b9d4216
same-name wrong index columns/sorting/opclass fail pre and post as specified
ORM metadata == independent literal contract
real PostgreSQL == independent literal contract
alembic check at c8e4f1a26b73 reports no new operations
```

Do not derive expected definitions from ORM, migration, or `scripts.check_schema`; all expected facts remain literal in `tests/migrations/final_contract.py` and deployment tests.

- [ ] **Step 5: Run the focused final gate**

Confirm there is no other pytest/Alembic process, then run serially:

```bash
uv run pytest -q tests/migrations tests/deployment tests/features/access_logs tests/features/short_links tests/architecture
uv run alembic heads
uv run alembic history --verbose
make -n migrate
docker compose --env-file .env.example config --quiet
uv run python -m compileall -q app scripts tests
git diff --check
```

Expected: all focused tests pass, the sole head is `c8e4f1a26b73`, history is linear, `make migrate` remains preflight -> upgrade -> postflight, and Compose resolves successfully.

- [ ] **Step 6: Run exactly one final serial full suite**

After confirming no pytest process is active, run:

```bash
uv run pytest -q --cache-clear
```

Capture an explicit exit code and final pytest summary. Do not launch `--lf` or a second suite while the first process remains alive. Confirm the PostgreSQL catalog contains zero databases matching `shorturl_migration_%` after completion.

- [ ] **Step 7: Commit the final gate**

```bash
git add docs/deployment.md tests/deployment tests/migrations
git commit -m "docs: finalize access-log search operations"
```

Record in the task report: focused/full commands and exact summaries, sole head, linear history, Compose result, compile result, diff check, unchanged hashes for migrations at or before `a73f0b9d4216`, and zero disposable-database residue.
