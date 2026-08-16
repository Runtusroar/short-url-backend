# MaxMind Insights and Proxy Intelligence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add cost-controlled MaxMind GeoIP Insights proxy detection to redirect decisions, with Redis caching and circuit breaking, fail-open rule evaluation, and explainable access-log facts.

**Architecture:** Keep the paid HTTP adapter in `app/integrations/maxmind/insights.py`, cost and availability policy in `app/features/redirect/proxy_intelligence.py`, and rule/transaction ownership in the redirect service. The redirect workflow first compares proxy and non-proxy rule outcomes, calls the intelligence service only when that fact can change the decision, then re-reads decision data before the final locked selection and access-log commit.

**Tech Stack:** Python 3.11, FastAPI, SQLAlchemy 2 async, PostgreSQL 15, Alembic, Redis 7/redis-py async, `geoip2==4.8.0`, Pydantic Settings, pytest/pytest-asyncio.

## Global Constraints

- Keep country lookup on the local GeoLite2 Country database; use MaxMind GeoIP Insights only for proxy/anonymizer facts.
- Read only the current Insights `anonymizer` object; do not use deprecated `traits` proxy fields.
- Treat `is_anonymous` as `bool | None`; unknown must never be coerced to `False`.
- Query Insights only when an active rule outcome differs between proxy and non-proxy evaluation and the user agent is not a bot.
- Bots make no paid call and are recorded as assumed proxy.
- Disabled Insights, invalid/non-global IPs, Redis failure, breaker state, lock contention, and MaxMind failure are fail-open; blacklist decisions remain blocking.
- If proxy status is unknown, evaluate both proxy states: allow if either branch allows, deny only if both deny, and choose the stable higher-priority matching rule when both allow.
- Do not call MaxMind when Redis is unavailable; avoiding an uncontrolled paid call is part of the contract.
- Success cache TTL is 86400 seconds, per-IP temporary-error TTL is 120 seconds, lock TTL is 5 seconds, credential/funds/permission breaker TTL is 1800 seconds, and rate-limit breaker TTL is 60 seconds.
- Use Redis keys `geoip:insights:v1:{canonical_ip}`, `geoip:insights:error:v1:{canonical_ip}`, `maxmind:insights:disabled:v1`, and `geoip:insights:lock:v1:{canonical_ip}`.
- Lock ownership uses a random token and atomic compare-and-delete; a lock loser waits and re-reads cache, then returns `lookup_contended` without a paid call if no result appears.
- Make one Insights request with timeout `1.5` seconds by default and no retry.
- When a successful MaxMind response cannot be cached, use and log the known response; do not downgrade it to unknown.
- Never expose or log `MAXMIND_LICENSE_KEY`; enabling Insights without both account ID and license key is a startup configuration error.
- MaxMind is not a readiness dependency; `/health/ready` remains based on PostgreSQL and Redis only.
- Do not hold database row locks or an open database transaction across Redis waits or the paid HTTP request.
- Persist only the decision-time facts in `access_logs`; do not create an IP-intelligence table.
- Do not add minFraud, dashboards, background retry workers, or Phase 7 audit features.
- Add one append-only Alembic revision after `c8e4f1a26b73`; do not edit historical revisions.
- PostgreSQL migration tests must use only UUID-named disposable databases and must leave zero `shorturl_migration_%` databases behind.
- Redis tests must use a unique per-test key prefix and exact-key cleanup; never use `FLUSHDB` or `FLUSHALL`.
- Run PostgreSQL/Alembic test commands serially; never run two test processes against the shared test services concurrently.

---

## File and Responsibility Map

**New production files**

- `alembic/versions/f84c2d7a901e_add_proxy_error_code.py` — append-only access-log column/check migration and safe downgrade preflight.
- `app/integrations/maxmind/insights.py` — official async GeoIP2 client wrapper, current `anonymizer` parsing, and sanitized upstream error classification.
- `app/features/redirect/proxy_intelligence.py` — canonical-IP validation, bot/disabled policy, Redis cache/breaker/lock protocol, and immutable assessment result.

**Modified production files**

- `app/models/enums.py` — stable persisted `ProxyErrorCode` enum.
- `app/models/access_log.py` — nullable `proxy_error_code` column and named CHECK.
- `app/core/config.py` — four Insights settings, enabled/credential validation, and secret-safe public summary.
- `app/main.py` — process-lifetime Insights client initialization/close without changing readiness.
- `app/features/redirect/router.py` — pass process-lifetime Redis and Insights dependencies to the single workflow.
- `app/features/redirect/service.py` — two-state rule preview, conditional assessment, fail-open selection, final re-read/locks, and complete proxy logging.
- `scripts/check_schema.py` — final check-constraint contract for `proxy_error_code`.
- `docker-compose.yml`, `.env.example` — explicit disabled-by-default runtime settings.
- `docs/deployment.md` — operator setup, failure semantics, monitoring, and rollback notes.

**New test files**

- `tests/migrations/test_phase6_proxy_intelligence.py` — real PostgreSQL upgrade/downgrade/atomicity contract.
- `tests/integrations/test_maxmind_insights.py` — adapter response and error mapping without paid network calls.
- `tests/features/redirect/test_proxy_intelligence.py` — Redis policy, cache, breaker, lock, and tri-state service tests.
- `tests/features/redirect/test_proxy_decisions.py` — rule-difference gating, fail-open outcomes, logging, and transaction-boundary tests.
- `tests/deployment/test_proxy_intelligence_docs.py` — configuration/runbook contract.

**Modified test infrastructure/contracts**

- `tests/migrations/final_contract.py`, `tests/migrations/test_final_contract.py`, `tests/migrations/test_phase4_full_path.py`, `tests/migrations/test_alembic_paths.py`, `tests/migrations/test_schema_baseline.py` — Phase 6 final schema and linear-path parity.
- `tests/deployment/test_schema_check.py`, `tests/deployment/test_deployment_config.py` — pre/post schema drift and Compose environment.
- `tests/core/test_config.py` — credential validation and secret redaction.
- `tests/features/redirect/test_redirect.py`, `tests/features/redirect/test_redirect_service_edge.py` — end-to-end redirect facts and commit-before-error behavior.
- `tests/architecture/test_redirect_integrations_layout.py` — module ownership and workflow signature.

---

### Task 1: Append-Only Proxy Error Schema Contract

**Files:**
- Create: `alembic/versions/f84c2d7a901e_add_proxy_error_code.py`
- Create: `tests/migrations/test_phase6_proxy_intelligence.py`
- Modify: `app/models/enums.py`
- Modify: `app/models/access_log.py`
- Modify: `scripts/check_schema.py`
- Modify: `tests/migrations/final_contract.py`
- Modify: `tests/migrations/test_final_contract.py`
- Modify: `tests/migrations/test_phase4_full_path.py`
- Modify: `tests/migrations/test_alembic_paths.py`
- Modify: `tests/migrations/test_schema_baseline.py`
- Modify: `tests/deployment/test_schema_check.py`

**Interfaces:**
- Consumes: Alembic parent revision `c8e4f1a26b73`; disposable fixture `migration_database_url: str`; existing `AccessLog` ORM model.
- Produces: revision `f84c2d7a901e`; `ProxyErrorCode(StrEnum)`; nullable `AccessLog.proxy_error_code: str | None`; named constraint `ck_access_logs_proxy_error_code`.

- [ ] **Step 1: Write the failing migration and ORM contract tests**

Add a real disposable-PostgreSQL test that upgrades `c8e4f1a26b73 -> f84c2d7a901e`, inspects the nullable `VARCHAR(32)` column/check, verifies historic rows stay `NULL`, and verifies all 13 allowed values. Add a downgrade test that first proves all-null rows can downgrade, then sets `timeout` and asserts the downgrade fails before any DDL with the revision and column unchanged.

```python
PROXY_ERROR_CODES = {
    "disabled", "redis_unavailable", "auth_failed", "insufficient_funds",
    "permission_denied", "rate_limited", "timeout", "upstream_error",
    "invalid_response", "ip_not_found", "invalid_ip", "non_global_ip",
    "lookup_contended",
}

def _scalar(database_url: str, statement: str) -> object:
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            return connection.scalar(text(statement))
    finally:
        engine.dispose()

def _execute(database_url: str, statement: str) -> None:
    engine = create_engine(database_url)
    try:
        with engine.begin() as connection:
            connection.execute(text(statement))
    finally:
        engine.dispose()

def _seed_access_log(database_url: str) -> None:
    engine = create_engine(database_url)
    try:
        with engine.begin() as connection:
            connection.execute(text("""
                WITH new_user AS (
                    INSERT INTO users
                        (username, password_hash, role, is_active, created_at, updated_at)
                    VALUES ('phase6-owner', 'hash', 'admin', true, now(), now())
                    RETURNING id
                ), new_domain AS (
                    INSERT INTO domains
                        (name, timezone, is_active, is_default, created_at, updated_at)
                    VALUES ('phase6.example.test', 'UTC', true, false, now(), now())
                    RETURNING id
                ), new_link AS (
                    INSERT INTO short_links
                        (domain_id, short_code, is_custom_alias, name, owner_id,
                         is_active, default_action, created_at, updated_at)
                    SELECT new_domain.id, 'phase6', false, 'Phase 6', new_user.id,
                           true, 'allow', now(), now()
                    FROM new_user, new_domain
                    RETURNING id, domain_id
                )
                INSERT INTO access_logs
                    (short_link_id, domain_id, result, access_date, dedup_bucket,
                     decision_reason, request_method, proxy_check_status)
                SELECT id, domain_id, 'allowed', current_date, 1,
                       'default_action', 'GET', 'skipped'
                FROM new_link
            """))
    finally:
        engine.dispose()

def test_phase6_upgrade_and_representable_downgrade(migration_database_url):
    run_alembic(migration_database_url, "upgrade", "c8e4f1a26b73")
    _seed_access_log(migration_database_url)
    run_alembic(migration_database_url, "upgrade", "f84c2d7a901e")
    contract = get_schema_contract(migration_database_url)
    assert contract["columns"]["access_logs"]["proxy_error_code"] == {
        "type": "VARCHAR(32)", "nullable": True, "default": None,
        "timezone": None,
    }
    assert "ck_access_logs_proxy_error_code" in contract["checks"]["access_logs"]
    assert _scalar(migration_database_url, "SELECT proxy_error_code FROM access_logs") is None
    run_alembic(migration_database_url, "downgrade", "c8e4f1a26b73")

def test_phase6_downgrade_rejects_persisted_error_before_writes(migration_database_url):
    run_alembic(migration_database_url, "upgrade", "f84c2d7a901e")
    _seed_access_log(migration_database_url)
    _execute(migration_database_url, "UPDATE access_logs SET proxy_error_code = 'timeout'")
    with pytest.raises(CalledProcessError) as exc_info:
        run_alembic(migration_database_url, "downgrade", "c8e4f1a26b73")
    assert "proxy error downgrade invariant" in (
        exc_info.value.stdout + exc_info.value.stderr
    )
    assert _scalar(migration_database_url, "SELECT version_num FROM alembic_version") == "f84c2d7a901e"
    assert "proxy_error_code" in get_schema_contract(migration_database_url)["columns"]["access_logs"]
```

Parameterize an additional direct insert/update test over every member of `PROXY_ERROR_CODES` and assert an unknown value raises `IntegrityError`.

- [ ] **Step 2: Run the new tests and verify the revision/field are missing**

Run: `uv run pytest -q tests/migrations/test_phase6_proxy_intelligence.py tests/migrations/test_final_contract.py tests/deployment/test_schema_check.py`

Expected: FAIL because revision `f84c2d7a901e`, `AccessLog.proxy_error_code`, and `ck_access_logs_proxy_error_code` do not exist.

- [ ] **Step 3: Add the stable enum and ORM field/check**

```python
class ProxyErrorCode(StrEnum):
    DISABLED = "disabled"
    REDIS_UNAVAILABLE = "redis_unavailable"
    AUTH_FAILED = "auth_failed"
    INSUFFICIENT_FUNDS = "insufficient_funds"
    PERMISSION_DENIED = "permission_denied"
    RATE_LIMITED = "rate_limited"
    TIMEOUT = "timeout"
    UPSTREAM_ERROR = "upstream_error"
    INVALID_RESPONSE = "invalid_response"
    IP_NOT_FOUND = "ip_not_found"
    INVALID_IP = "invalid_ip"
    NON_GLOBAL_IP = "non_global_ip"
    LOOKUP_CONTENDED = "lookup_contended"
```

```python
proxy_error_code = Column(String(32), nullable=True)

CheckConstraint(
    "proxy_error_code IS NULL OR proxy_error_code = ANY "
    "(ARRAY['disabled', 'redis_unavailable', 'auth_failed', "
    "'insufficient_funds', 'permission_denied', 'rate_limited', 'timeout', "
    "'upstream_error', 'invalid_response', 'ip_not_found', 'invalid_ip', "
    "'non_global_ip', 'lookup_contended'])",
    name="ck_access_logs_proxy_error_code",
)
```

- [ ] **Step 4: Implement the append-only migration with write-before-check protection**

```python
revision = "f84c2d7a901e"
down_revision = "c8e4f1a26b73"

def upgrade() -> None:
    op.add_column("access_logs", sa.Column("proxy_error_code", sa.String(32), nullable=True))
    op.create_check_constraint(
        "ck_access_logs_proxy_error_code",
        "access_logs",
        "proxy_error_code IS NULL OR proxy_error_code = ANY "
        "(ARRAY['disabled', 'redis_unavailable', 'auth_failed', "
        "'insufficient_funds', 'permission_denied', 'rate_limited', 'timeout', "
        "'upstream_error', 'invalid_response', 'ip_not_found', 'invalid_ip', "
        "'non_global_ip', 'lookup_contended'])",
    )

def downgrade() -> None:
    count = op.get_bind().execute(sa.text(
        "SELECT count(*) FROM access_logs WHERE proxy_error_code IS NOT NULL"
    )).scalar_one()
    if count:
        raise RuntimeError("proxy error downgrade invariant")
    op.drop_constraint("ck_access_logs_proxy_error_code", "access_logs", type_="check")
    op.drop_column("access_logs", "proxy_error_code")
```

- [ ] **Step 5: Extend independent final-schema and preflight facts**

Add the column and the full literal CHECK SQL to `tests/migrations/final_contract.py`; make ORM and real PostgreSQL parity compare it. Extend `scripts/check_schema.py` so postflight rejects a missing or same-named-but-different check, while preflight accepts its absence on `c8e4f1a26b73` and rejects a conflicting same-name check.

```python
FINAL_COLUMNS["access_logs"]["proxy_error_code"] = {
    "type": "VARCHAR", "length": 32, "nullable": True, "default": None,
}
FINAL_CHECKS["access_logs"]["ck_access_logs_proxy_error_code"] = (
    "proxy_error_code IS NULL OR proxy_error_code = ANY "
    "(ARRAY['disabled', 'redis_unavailable', 'auth_failed', "
    "'insufficient_funds', 'permission_denied', 'rate_limited', 'timeout', "
    "'upstream_error', 'invalid_response', 'ip_not_found', 'invalid_ip', "
    "'non_global_ip', 'lookup_contended'])"
)
```

- [ ] **Step 6: Extend every supported Alembic path**

Update path expectations so empty database, the Phase 3 baseline, every Phase 4 revision, `c8e4f1a26b73`, and head all upgrade to `f84c2d7a901e`. Add `head -> c8e4f1a26b73 -> head` with representative rows whose `proxy_error_code` is `NULL`; assert exact row counts/IDs remain unchanged.

- [ ] **Step 7: Run focused schema verification**

Run: `uv run pytest -q tests/migrations tests/deployment/test_schema_check.py`

Expected: PASS; the output includes the new Phase 6 migration tests, final ORM/PostgreSQL parity, and all upgrade paths.

- [ ] **Step 8: Commit Task 1**

```bash
git add alembic/versions/f84c2d7a901e_add_proxy_error_code.py app/models/enums.py app/models/access_log.py scripts/check_schema.py tests/migrations tests/deployment/test_schema_check.py
git commit -m "feat: add proxy intelligence log contract"
```

### Task 2: Configuration, Official Insights Adapter, and Lifespan Ownership

**Files:**
- Create: `app/integrations/maxmind/insights.py`
- Create: `tests/integrations/test_maxmind_insights.py`
- Modify: `app/core/config.py`
- Modify: `app/main.py`
- Modify: `docker-compose.yml`
- Modify: `.env.example`
- Modify: `tests/core/test_config.py`
- Modify: `tests/deployment/test_deployment_config.py`
- Modify: `tests/architecture/test_redirect_integrations_layout.py`

**Interfaces:**
- Consumes: official `geoip2.webservice.AsyncClient(account_id: int, license_key: str, timeout: float)` and `await client.insights(ip_address)` / `await client.close()`.
- Produces: `InsightsErrorKind`, `InsightsLookupError`, immutable `InsightsResult`, `MaxMindInsightsClient.lookup(ip_address) -> InsightsResult`, `MaxMindInsightsClient.close() -> None`, and `app.state.maxmind_insights`.

- [ ] **Step 1: Write failing configuration and adapter tests**

Test disabled defaults, enabled credentials, positive timeout, secret redaction, lifecycle close, current `anonymizer` parsing, and every sanitized mapping. Construct `geoip2.models.Insights` from fixture JSON; do not call the live service.

```python
ANONYMIZER_RESPONSE = {
    "anonymizer": {
        "is_anonymous": True,
        "is_anonymous_vpn": True,
        "is_hosting_provider": False,
        "is_public_proxy": False,
        "is_residential_proxy": True,
        "is_tor_exit_node": False,
    }
}

async def test_lookup_reads_only_current_anonymizer(monkeypatch):
    record = Insights({**minimal_insights_payload(), **ANONYMIZER_RESPONSE})
    monkeypatch.setattr(fake_sdk, "insights", AsyncMock(return_value=record))
    result = await client.lookup("8.8.8.8")
    assert result == InsightsResult(
        is_anonymous=True,
        proxy_types=("anonymous_vpn", "residential_proxy"),
    )
```

- [ ] **Step 2: Run tests and verify missing settings/module failures**

Run: `uv run pytest -q tests/core/test_config.py tests/integrations/test_maxmind_insights.py tests/deployment/test_deployment_config.py tests/architecture/test_redirect_integrations_layout.py`

Expected: FAIL with missing `maxmind_insights_enabled`, missing `app.integrations.maxmind.insights`, and absent Compose environment entries.

- [ ] **Step 3: Add validated, secret-safe settings**

```python
maxmind_insights_enabled: bool = False
maxmind_account_id: int | None = None
maxmind_license_key: str | None = None
maxmind_timeout_seconds: float = 1.5

@model_validator(mode="after")
def validate_maxmind_insights(self):
    if self.maxmind_timeout_seconds <= 0:
        raise ValueError("MAXMIND_TIMEOUT_SECONDS must be greater than zero")
    if self.maxmind_insights_enabled and (
        self.maxmind_account_id is None or not (self.maxmind_license_key or "").strip()
    ):
        raise ValueError(
            "MAXMIND_ACCOUNT_ID and MAXMIND_LICENSE_KEY are required when "
            "MAXMIND_INSIGHTS_ENABLED is true"
        )
    return self
```

Add only `maxmind_insights_enabled` and `maxmind_timeout_seconds` to `public_summary()`; assert account ID and license key are absent from both values and keys.

- [ ] **Step 4: Implement the official adapter and sanitized error types**

```python
class InsightsErrorKind(StrEnum):
    AUTH_FAILED = "auth_failed"
    INSUFFICIENT_FUNDS = "insufficient_funds"
    PERMISSION_DENIED = "permission_denied"
    RATE_LIMITED = "rate_limited"
    TIMEOUT = "timeout"
    UPSTREAM_ERROR = "upstream_error"
    INVALID_RESPONSE = "invalid_response"
    IP_NOT_FOUND = "ip_not_found"

@dataclass(frozen=True)
class InsightsResult:
    is_anonymous: bool
    proxy_types: tuple[str, ...]

class InsightsLookupError(RuntimeError):
    def __init__(self, kind: InsightsErrorKind):
        super().__init__(kind.value)
        self.kind = kind
```

`lookup()` must catch `AuthenticationError`, `OutOfQueriesError`, `PermissionRequiredError`, `AddressNotFoundError`, timeout/aiohttp transport errors, and `HTTPError`. Map HTTP 429 to `RATE_LIMITED`, 500–599 to `UPSTREAM_ERROR`, and all malformed/missing `anonymizer` payloads to `INVALID_RESPONSE`. Extract proxy types in this exact stable order:

```python
ANONYMIZER_FIELDS = (
    ("is_anonymous_vpn", "anonymous_vpn"),
    ("is_hosting_provider", "hosting_provider"),
    ("is_public_proxy", "public_proxy"),
    ("is_residential_proxy", "residential_proxy"),
    ("is_tor_exit_node", "tor_exit_node"),
)
```

- [ ] **Step 5: Own one client per process in FastAPI lifespan**

```python
insights = None
if settings.maxmind_insights_enabled:
    insights = MaxMindInsightsClient(
        account_id=settings.maxmind_account_id,
        license_key=settings.maxmind_license_key,
        timeout=settings.maxmind_timeout_seconds,
    )
app.state.maxmind_insights = insights
try:
    yield
finally:
    if insights is not None:
        await insights.close()
    app.state.maxmind_insights = None
```

Keep readiness unchanged: it must not call or report MaxMind.

- [ ] **Step 6: Add explicit disabled-by-default deployment values**

```dotenv
MAXMIND_INSIGHTS_ENABLED=false
MAXMIND_ACCOUNT_ID=
MAXMIND_LICENSE_KEY=
MAXMIND_TIMEOUT_SECONDS=1.5
```

Pass the same four values into the Compose app service. Tests must prove the license key is passed only through environment configuration and never printed by settings summaries.

- [ ] **Step 7: Run focused configuration/adapter verification**

Run: `uv run pytest -q tests/core/test_config.py tests/integrations/test_maxmind_insights.py tests/deployment/test_deployment_config.py tests/architecture/test_redirect_integrations_layout.py`

Expected: PASS; no network access occurs.

- [ ] **Step 8: Commit Task 2**

```bash
git add app/core/config.py app/main.py app/integrations/maxmind/insights.py docker-compose.yml .env.example tests/core/test_config.py tests/integrations/test_maxmind_insights.py tests/deployment/test_deployment_config.py tests/architecture/test_redirect_integrations_layout.py
git commit -m "feat: add maxmind insights adapter"
```

### Task 3: Redis-Governed Proxy Intelligence Service

**Files:**
- Create: `app/features/redirect/proxy_intelligence.py`
- Create: `tests/features/redirect/test_proxy_intelligence.py`

**Interfaces:**
- Consumes: `Redis | None`; `MaxMindInsightsClient | None`; `InsightsResult`; `InsightsLookupError`; `ProxyErrorCode`; `ProxyCheckStatus`; `ProxySource`.
- Produces: immutable `ProxyAssessment`; `assess_proxy(redis, insights, ip_address, platform, *, enabled, lookup_required, keyspace=None) -> ProxyAssessment`; `RedisKeyspace` and a validated JSON decoder used by tests.

- [ ] **Step 1: Write failing pure-policy tests**

Cover disabled, bot, rule-equivalent skip, invalid IP, private/loopback/reserved/non-global IP, valid canonical IPv4/IPv6, success cache parsing, malformed-cache fail-open, and exact status/source/error fields. Apply early exits in this order: bot assumption, rule-equivalent skip, globally disabled, invalid/non-global IP, then Redis. This makes every bot `assumed_bot`, every non-bot rule-equivalent request `skipped`, and a proxy-sensitive request with the feature disabled `error/disabled`.

```python
@dataclass(frozen=True)
class ProxyAssessment:
    is_anonymous: bool | None
    proxy_types: tuple[str, ...]
    status: ProxyCheckStatus
    source: ProxySource | None
    error_code: ProxyErrorCode | None

    @classmethod
    def unknown(
        cls,
        error_code: ProxyErrorCode,
        source: ProxySource | None = None,
    ) -> "ProxyAssessment":
        return cls(None, (), ProxyCheckStatus.ERROR, source, error_code)

def test_bot_is_assumed_proxy_without_dependencies():
    result = await assess_proxy(
        redis=None, insights=None, ip_address="8.8.8.8",
        platform="bot", enabled=True, lookup_required=False,
    )
    assert result == ProxyAssessment(
        True, (), ProxyCheckStatus.ASSUMED_BOT,
        ProxySource.ASSUMED_BOT, None,
    )
```

- [ ] **Step 2: Write failing Redis protocol tests**

Use a UUID prefix such as `test:proxy-intelligence:{uuid}:` passed through the optional `keyspace` argument. Production calls omit it and therefore use an empty-prefix `RedisKeyspace`. Test positive and negative success values at TTL 86400, per-IP error TTL 120, 1800/60 breaker TTLs, `SET NX EX 5`, token-safe release, loser re-read, and zero adapter calls when Redis fails. Cleanup only the keys returned by that keyspace's `created_keys` set.

```python
@dataclass
class RedisKeyspace:
    prefix: str = ""
    created_keys: set[str] = field(default_factory=set)

    def _track(self, key: str) -> str:
        self.created_keys.add(key)
        return key

    def success(self, ip: str) -> str:
        return self._track(f"{self.prefix}geoip:insights:v1:{ip}")

    def error(self, ip: str) -> str:
        return self._track(f"{self.prefix}geoip:insights:error:v1:{ip}")

    def breaker(self) -> str:
        return self._track(f"{self.prefix}maxmind:insights:disabled:v1")

    def lock(self, ip: str) -> str:
        return self._track(f"{self.prefix}geoip:insights:lock:v1:{ip}")
```

- [ ] **Step 3: Run tests and verify the service is absent**

Run: `uv run pytest -q tests/features/redirect/test_proxy_intelligence.py`

Expected: FAIL because `app.features.redirect.proxy_intelligence` does not exist.

- [ ] **Step 4: Implement immutable results, canonicalization, and early exits**

```python
def _canonical_global_ip(value: str) -> tuple[str | None, ProxyErrorCode | None]:
    try:
        address = ipaddress.ip_address(value)
    except (TypeError, ValueError):
        return None, ProxyErrorCode.INVALID_IP
    if not address.is_global:
        return None, ProxyErrorCode.NON_GLOBAL_IP
    return str(address), None
```

Order early exits as bot, rule-equivalent skip, disabled, invalid/non-global, then Redis. Disabled must not touch Redis. Invalid/non-global must not call Redis or MaxMind.

- [ ] **Step 5: Implement validated cache and breaker records**

Use versioned JSON with only stable fields:

```json
{"v": 1, "is_anonymous": true, "proxy_types": ["anonymous_vpn"]}
{"v": 1, "error_code": "timeout"}
{"v": 1, "error_code": "insufficient_funds"}
```

Reject non-boolean `is_anonymous`, unknown proxy types, non-list types, unknown error codes, and wrong versions. A malformed cache returns unknown `ERROR/None/invalid_response` without a paid call. A valid success cache returns `CACHED/MAXMIND_INSIGHTS`; a valid MaxMind-originated error or breaker record returns `ERROR/MAXMIND_INSIGHTS` with the exact stored `ProxyErrorCode`, without an adapter call. Local policy failures (`disabled`, `redis_unavailable`, `invalid_ip`, `non_global_ip`, `lookup_contended`) use a null source.

- [ ] **Step 6: Implement the single-flight lock and adapter mapping**

```python
token = secrets.token_urlsafe(24)
acquired = await redis.set(lock_key, token, nx=True, ex=5)
if not acquired:
    for _ in range(10):
        await asyncio.sleep(0.05)
        cached = await _read_cached_assessment(redis, canonical_ip, keys)
        if cached is not None:
            return cached
    return ProxyAssessment.unknown(ProxyErrorCode.LOOKUP_CONTENDED)
try:
    result = await insights.lookup(canonical_ip)
finally:
    await redis.eval(COMPARE_DELETE_LUA, 1, lock_key, token)
```

Map auth/funds/permission errors to the 1800-second global breaker, rate limiting to the 60-second global breaker, and timeout/upstream/invalid-response/IP-not-found to the 120-second per-IP error. If the adapter succeeds and success-cache write fails, return `CHECKED/MAXMIND_INSIGHTS` with the known bool/types and emit one structured warning containing only error class and canonical IP—never credentials or upstream body.

- [ ] **Step 7: Verify exact cost-control behavior**

Run: `uv run pytest -q tests/features/redirect/test_proxy_intelligence.py`

Expected: PASS, including assertions that Redis failure produces `redis_unavailable` with zero adapter calls, and 20 simultaneous callers produce at most one adapter call.

- [ ] **Step 8: Commit Task 3**

```bash
git add app/features/redirect/proxy_intelligence.py tests/features/redirect/test_proxy_intelligence.py
git commit -m "feat: govern proxy lookups with redis"
```

### Task 4: Redirect Rule Gating, Fail-Open Decisions, and Explainable Logs

**Files:**
- Create: `tests/features/redirect/test_proxy_decisions.py`
- Modify: `app/features/redirect/service.py`
- Modify: `app/features/redirect/router.py`
- Modify: `tests/features/redirect/test_redirect.py`
- Modify: `tests/features/redirect/test_redirect_service_edge.py`
- Modify: `tests/architecture/test_redirect_integrations_layout.py`

**Interfaces:**
- Consumes: `assess_proxy(redis, insights, ip_address, platform, *, enabled, lookup_required, keyspace=None) -> ProxyAssessment`; `app.state.redis`; `app.state.maxmind_insights`; `AccessLog.proxy_error_code`.
- Produces: primitive immutable `RuleOutcome`; `proxy_assessment_required(non_proxy, proxy) -> bool`; `choose_fail_open_outcome(non_proxy, proxy) -> RuleOutcome`; revised `execute_redirect(db, host, short_code, client_ip, ua_string, referer, request_method, redis, insights) -> str`.

- [ ] **Step 1: Write failing pure rule-gating tests**

Use deterministic UUIDs/priorities. Require a lookup only when `(action, matched_rule_id)` differs, not merely because a proxy-specific rule exists.

```python
@dataclass(frozen=True)
class RuleOutcome:
    action: AccessAction
    matched_rule_id: UUID | None
    matched_rule_priority: int | None

def proxy_assessment_required(non_proxy: RuleOutcome, proxy: RuleOutcome) -> bool:
    return (non_proxy.action, non_proxy.matched_rule_id) != (
        proxy.action, proxy.matched_rule_id
    )

def _outcome_rank(outcome: RuleOutcome) -> tuple[int, int]:
    if outcome.matched_rule_id is None:
        return (sys.maxsize, (1 << 128) - 1)
    assert outcome.matched_rule_priority is not None
    return (outcome.matched_rule_priority, outcome.matched_rule_id.int)

def choose_fail_open_outcome(
    non_proxy: RuleOutcome,
    proxy: RuleOutcome,
) -> RuleOutcome:
    if non_proxy.action != proxy.action:
        return non_proxy if non_proxy.action == AccessAction.ALLOW else proxy
    return min((non_proxy, proxy), key=_outcome_rank)
```

Test all unknown combinations: allow/deny chooses allow; deny/allow chooses allow; deny/deny chooses stable higher-priority denial; allow/allow chooses stable higher-priority allow. Compare matched rules by `(priority, id)` and give the default action rank `(infinity, infinity)`. Because `RuleOutcome` stores only UUID/priority/action primitives, it remains safe after the preview transaction rolls back.

- [ ] **Step 2: Write failing workflow and logging tests**

Add real PostgreSQL tests for these exact rows:

- no proxy-sensitive outcome difference: zero Redis/MaxMind calls, `skipped/NULL/[]/NULL/NULL`;
- bot: zero paid calls, `assumed_bot/true/[]/assumed_bot/NULL`;
- cached non-proxy: `cached/false/[]/maxmind_insights/NULL`;
- live proxy: `checked/true/[types]/maxmind_insights/NULL`;
- Redis unavailable: `error/NULL/[]/NULL/redis_unavailable`;
- MaxMind timeout: `error/NULL/[]/maxmind_insights/timeout`;
- blacklist: blocks without Insights even when proxy rules differ and logs `skipped/NULL/[]/NULL/NULL`;
- 403 and 404: log commit occurs before the HTTP error is raised.

- [ ] **Step 3: Write a transaction-boundary regression**

Use a blocking fake Insights adapter. Start `execute_redirect`, wait until the fake enters `lookup`, then update/delete an eligible rule or target from a second `NullPool` session. Assert the second session is not waiting on a row lock during the external call. Release the fake, then assert the final decision uses re-read rules and the final locked target selection.

```python
await fake_insights.entered.wait()
statement = (
    update(AccessRule)
    .where(AccessRule.id == rule_id)
    .values(is_active=False)
)
await asyncio.wait_for(other_session.execute(statement), timeout=1)
await other_session.commit()
fake_insights.release.set()
decision = await redirect_task
assert decision == "https://allowed.example/new"
```

- [ ] **Step 4: Run the redirect tests and verify old assumed-bot logic fails**

Run: `uv run pytest -q tests/features/redirect/test_proxy_decisions.py tests/features/redirect/test_redirect.py tests/features/redirect/test_redirect_service_edge.py tests/architecture/test_redirect_integrations_layout.py`

Expected: FAIL because the workflow accepts only a single hard-coded `is_proxy`, always marks non-bots skipped, and does not accept Redis/Insights dependencies.

- [ ] **Step 5: Split preview evaluation from final target selection**

Make `evaluate_rules()` return primitive `RuleOutcome`. Add a non-locking preview query that loads active rules ordered by `(priority, id)`, evaluates both proxy states, records IDs/priorities/actions, and then explicitly ends the read transaction before calling `assess_proxy`. Copy every preview fact needed by the assessment into primitives before rollback; do not retain an ORM object for use during the external wait.

```python
non_proxy = evaluate_rules(rules, link, country, platform, referer, False)
proxy = evaluate_rules(rules, link, country, platform, referer, True)
needs_lookup = proxy_assessment_required(non_proxy, proxy)
await db.rollback()  # end preview transaction before Redis/HTTP wait
assessment = await assess_proxy(
    redis,
    insights,
    client_ip,
    platform,
    enabled=settings.maxmind_insights_enabled,
    lookup_required=needs_lookup,
)
```

The separate `lookup_required` flag must not be confused with global `enabled`: global disabled returns `disabled` only for a proxy-sensitive non-bot request, while a rule-equivalent non-bot request returns `skipped` with no error.

- [ ] **Step 6: Implement known and unknown final outcome selection**

Re-resolve domain/link, re-check active blacklist state, and re-query active rules after assessment. If `assessment.is_anonymous` is known, evaluate that branch. If unknown, evaluate both current branches and call `choose_fail_open_outcome`. An initially blacklisted request bypasses assessment; a final blacklist match always overrides the rule outcome. If an initial blacklist is concurrently removed, continue with the skipped/unknown assessment and fail-open rule selection rather than making a late paid call. Lock only the final matched rule and eligible targets with `FOR KEY SHARE` until `_log_access` commits.

```python
if assessment.is_anonymous is None:
    outcome = choose_fail_open_outcome(current_non_proxy, current_proxy)
else:
    outcome = current_proxy if assessment.is_anonymous else current_non_proxy
```

- [ ] **Step 7: Persist the complete assessment without inference**

Change `_log_access` to accept `ProxyAssessment` and write its facts directly:

```python
proxy_check_status=assessment.status,
is_anonymous=assessment.is_anonymous,
proxy_types=list(assessment.proxy_types),
proxy_source=assessment.source,
proxy_error_code=assessment.error_code,
```

Do not infer anonymous status from user agent in `_log_access`. Preserve the single commit before `PermissionDeniedError`/`NotFoundError` behavior.

- [ ] **Step 8: Pass process clients through the router's one workflow call**

```python
target_url = await execute_redirect(
    db, host, short_code, client_ip, ua_string, referer, request.method,
    request.app.state.redis,
    request.app.state.maxmind_insights,
)
```

Update the architecture test so the router still imports only `execute_redirect` from the feature service and does not import MaxMind policy modules directly.

- [ ] **Step 9: Run focused redirect verification**

Run: `uv run pytest -q tests/features/redirect tests/architecture/test_redirect_integrations_layout.py tests/architecture/test_access_logs_layout.py`

Expected: PASS, including transaction-boundary, blacklist, bot, cached/live/error, and commit-before-error cases.

- [ ] **Step 10: Commit Task 4**

```bash
git add app/features/redirect/service.py app/features/redirect/router.py tests/features/redirect tests/architecture/test_redirect_integrations_layout.py tests/architecture/test_access_logs_layout.py
git commit -m "feat: apply proxy intelligence to redirects"
```

### Task 5: Operations Documentation and Final Release Gate

**Files:**
- Create: `tests/deployment/test_proxy_intelligence_docs.py`
- Modify: `docs/deployment.md`
- Modify: `.env.example`
- Modify: `tests/deployment/test_deployment_docs.py`

**Interfaces:**
- Consumes: the four settings, Redis TTL/key contract, stable error codes, fail-open semantics, revision `f84c2d7a901e`, and existing Make/Compose deployment commands.
- Produces: an Ubuntu operator runbook and final verified Phase 6 branch.

- [ ] **Step 1: Write the failing documentation contract**

```python
def test_runbook_documents_proxy_intelligence_operations():
    text = DEPLOYMENT_DOC.read_text()
    for value in (
        "MAXMIND_INSIGHTS_ENABLED", "MAXMIND_ACCOUNT_ID",
        "MAXMIND_LICENSE_KEY", "MAXMIND_TIMEOUT_SECONDS",
        "fail-open", "proxy_error_code", "insufficient_funds",
        "redis_unavailable", "make migrate",
    ):
        assert value in text
    assert "GeoIP Insights" in text
    assert "not a readiness dependency" in text
```

- [ ] **Step 2: Run the docs test and verify the operator section is missing**

Run: `uv run pytest -q tests/deployment/test_proxy_intelligence_docs.py`

Expected: FAIL because `docs/deployment.md` does not describe Insights configuration or failure behavior.

- [ ] **Step 3: Document enablement and secret handling**

Add exact production steps: create/fund an account that has GeoIP Insights permission; put account ID/license key only in the machine-local `.env`; set enabled true; run `docker compose --env-file .env config` without publishing its secret-bearing output; run `make migrate`, `make up`, and verify health. State that disabling the feature requires only `MAXMIND_INSIGHTS_ENABLED=false` and an app restart; it does not remove log history.

- [ ] **Step 4: Document runtime/failure semantics and monitoring queries**

Describe rule-difference gating, bot assumption, Redis cost guard, TTLs/breakers, fail-open selection, and the fact that MaxMind does not affect readiness. Add a safe SQL query grouped by `proxy_check_status`, `proxy_error_code`, and `proxy_source`, plus alert guidance for sustained `redis_unavailable`, `auth_failed`, `insufficient_funds`, `permission_denied`, and `rate_limited` counts. Do not print IPs or credentials in the example.

- [ ] **Step 5: Run all focused Phase 6 suites serially**

First confirm no pytest/Alembic process is active. Then run exactly one process:

Run: `uv run pytest -q tests/migrations tests/deployment tests/core/test_config.py tests/integrations tests/features/redirect tests/architecture`

Expected: PASS with no errors; retain the exact count and duration in the task report.

- [ ] **Step 6: Run the final full suite once**

Again confirm the serial lane is clear, then persist output and exit status:

```bash
uv run pytest -q --cache-clear > /tmp/phase6-full.log 2>&1
test $? -eq 0
tail -n 2 /tmp/phase6-full.log
```

Expected: PASS with zero failures/errors. The existing duplicate redirect OpenAPI operation-ID warning may remain; no new warnings are accepted.

- [ ] **Step 7: Run migration, Compose, and static release gates**

Run these serially:

```bash
uv run alembic heads
uv run alembic history --verbose
make -n migrate
docker compose --env-file .env.example config >/dev/null
uv run python -m compileall -q app scripts tests
git diff --check
```

Expected: sole head `f84c2d7a901e`; linear parent `c8e4f1a26b73`; `make -n migrate` orders preflight -> upgrade -> postflight; all remaining commands exit 0.

- [ ] **Step 8: Verify a disposable database and residue count**

Using `tests.migrations.support.disposable_migration_database`, create one UUID database, run `alembic upgrade head`, `alembic check`, and `scripts/check_schema.py --mode post`; let the context manager drop it. Query `pg_database` through the configured test PostgreSQL endpoint and assert:

```sql
SELECT count(*) FROM pg_database WHERE datname LIKE 'shorturl_migration_%';
```

Expected: `0`. Never point Alembic at `shorturl_test`.

- [ ] **Step 9: Request independent code review and fix only evidenced findings**

Review the exact Phase 6 commit range against the approved design. The reviewer must inspect paid-call gating, Redis failure behavior, lock ownership, unknown fail-open selection, transaction boundaries, migration atomicity, secret redaction, and no out-of-scope table/workers. Any accepted fix starts with a failing regression test and repeats the affected focused gate.

- [ ] **Step 10: Commit Task 5**

```bash
git add docs/deployment.md tests/deployment/test_proxy_intelligence_docs.py tests/deployment/test_deployment_docs.py .env.example
git commit -m "docs: operate proxy intelligence safely"
```

## Final Acceptance Checklist

- [ ] A non-bot request makes no paid call when proxy and non-proxy outcomes have the same action and matched-rule ID.
- [ ] Bot, disabled, invalid/non-global IP, Redis unavailable, breaker, lock contention, cached success, live success, and all upstream failures persist the approved status/source/error facts.
- [ ] Unknown proxy state allows if either rule branch allows; blacklist still blocks.
- [ ] Both proxy and non-proxy successful Insights results cache for 86400 seconds.
- [ ] Redis outage makes zero MaxMind calls; cache-write failure after a successful call retains the known result.
- [ ] No external wait occurs while holding database row locks or an open database transaction.
- [ ] GET and HEAD still return 302 when allowed, and 403/404 paths commit one access log before raising.
- [ ] Only `anonymizer` fields are parsed; tests make no paid network call.
- [ ] License key is absent from public configuration and logs; MaxMind remains absent from readiness checks.
- [ ] Alembic has one head `f84c2d7a901e`, all supported paths pass, downgrade refusal is pre-write and sanitized, and disposable residue is zero.
- [ ] Focused and full serial suites, Compose rendering, compileall, and diff checks all pass.
