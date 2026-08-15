# Environment and Proxy Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Establish one validated dev/prod configuration model, a single Makefile-driven Docker Compose workflow, and a trustworthy Nginx-to-FastAPI client identity boundary without adding paid MaxMind calls yet.

**Architecture:** Keep the current application behavior and public API intact while moving environment differences into Pydantic Settings and `.env`. Nginx overwrites `X-Real-IP`, one shared helper supplies the same validated address to logging and rate limiting, and HTTP forwarding headers are no longer treated as evidence that an end user is using a proxy. This is phase 1 of the approved redesign; directory-wide moves, schema changes, log filters, and GeoIP Insights are separate independently testable phases.

**Tech Stack:** Python 3.11, FastAPI 0.111, Pydantic Settings 2.3, SQLAlchemy 2.0 async, Redis 5, fastapi-limiter, pytest 8, Docker Compose, Nginx

## Global Constraints

- Use one codebase, one Dockerfile, and one `docker-compose.yml` for dev and prod.
- Each machine has one untracked `.env`; Git tracks only `.env.example`.
- Makefile is the documented operator interface; environment-specific Make targets are not added.
- Production FastAPI binds to `127.0.0.1:18000`; PostgreSQL and Redis also bind only to `127.0.0.1`.
- Production rejects a default/short secret, insecure cookies, wildcard CORS, and missing database or Redis configuration.
- Nginx overwrites `Host`, `X-Real-IP`, `X-Forwarded-For`, and `X-Forwarded-Proto`.
- Application code trusts only validated `X-Real-IP` when `TRUST_PROXY_HEADERS=true`.
- Forwarding headers never determine whether the end user is a VPN/proxy user.
- UA Bot detection runs before mobile/tablet/PC detection.
- Until GeoIP Insights is implemented, Bot requests are treated as proxy requests and human requests are not classified as proxies.
- Existing API routes and response formats remain compatible in this phase.
- Existing data volumes and Docker volume names must not change.
- Every task follows red-green-refactor and ends with an isolated commit.

---

## File Map

### Files created in this phase

- `app/core/__init__.py` — package marker for infrastructure helpers that already have final architecture boundaries.
- `app/core/client_ip.py` — validates and resolves the client IP from the direct socket or trusted Nginx header.
- `scripts/check_config.py` — loads Settings, applies production validation, and prints only non-secret configuration metadata.
- `tests/test_config.py` — Settings defaults and production safety tests.
- `tests/test_client_ip.py` — trusted/untrusted/invalid forwarding header tests.
- `tests/test_deployment_config.py` — repository contract tests for `.env.example`, Compose loopback bindings, and Make targets.
- `docs/deployment.md` — manual dev and Ubuntu/Nginx deployment runbook.
- `tests/test_deployment_docs.py` — verifies the runbook uses the safe one-proxy Nginx header contract.

### Files modified in this phase

- `app/config.py` — typed environment, CORS, cookie, logging, and proxy trust settings with production invariants.
- `app/main.py` — shared client identifier, configured CORS, application Redis state, and live/ready health endpoints.
- `app/routers/auth.py` — cookie `secure` flag comes from Settings.
- `app/routers/redirect.py` — shared client IP helper and removal of header-based proxy detection.
- `app/services/ua.py` — Bot classification precedes device classification.
- `.env.example` — complete safe configuration schema.
- `docker-compose.yml` — parameterized credentials, loopback-only ports, health check, restart policy, and app settings.
- `Makefile` — `env` and `check-config`; `up` validates before starting.
- `tests/test_main.py` — live and ready health behavior.
- `tests/test_redirect.py` — interim Bot-as-proxy policy and forwarding-header regression coverage.
- `tests/test_services_edge.py` — Bot classification precedence.

---

### Task 1: Typed Environment Configuration and Production Guardrails

**Files:**
- Modify: `app/config.py`
- Create: `scripts/check_config.py`
- Modify: `.env.example`
- Create: `tests/test_config.py`

**Interfaces:**
- Produces: `Settings(app_env, cookie_secure, cors_origins, log_level, trust_proxy_headers)`.
- Produces: `Settings.cors_origin_list -> list[str]`.
- Produces: `Settings.public_summary() -> dict[str, str | bool]` with no secret values.
- Consumes: no new interfaces.

- [ ] **Step 1: Write failing Settings tests**

Create `tests/test_config.py`:

```python
import pytest
from pydantic import ValidationError

from app.config import Settings


def make_settings(**overrides):
    values = {
        "app_env": "dev",
        "database_url": "postgresql+psycopg://user:pass@db:5432/app",
        "redis_url": "redis://redis:6379/0",
        "secret_key": "dev-secret",
        "cookie_secure": False,
        "cors_origins": "http://localhost:3000,http://localhost:5173",
        "log_level": "DEBUG",
        "trust_proxy_headers": False,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def test_dev_settings_parse_cors_origins():
    settings = make_settings()
    assert settings.app_env == "dev"
    assert settings.cors_origin_list == [
        "http://localhost:3000",
        "http://localhost:5173",
    ]


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"secret_key": "dev-secret"}, "SECRET_KEY"),
        ({"cookie_secure": False}, "COOKIE_SECURE"),
        ({"cors_origins": "*"}, "CORS_ORIGINS"),
        ({"database_url": ""}, "DATABASE_URL"),
        ({"redis_url": ""}, "REDIS_URL"),
    ],
)
def test_prod_rejects_unsafe_configuration(overrides, message):
    base = {
        "app_env": "prod",
        "secret_key": "x" * 32,
        "cookie_secure": True,
        "cors_origins": "https://admin.example.com",
        "database_url": "postgresql+psycopg://user:pass@db:5432/app",
        "redis_url": "redis://redis:6379/0",
    }
    base.update(overrides)
    with pytest.raises(ValidationError, match=message):
        make_settings(**base)


def test_prod_accepts_safe_configuration():
    settings = make_settings(
        app_env="prod",
        secret_key="x" * 32,
        cookie_secure=True,
        cors_origins="https://admin.example.com",
        log_level="INFO",
        trust_proxy_headers=True,
    )
    assert settings.cors_origin_list == ["https://admin.example.com"]
    assert settings.public_summary() == {
        "app_env": "prod",
        "cookie_secure": True,
        "cors_origins": "https://admin.example.com",
        "log_level": "INFO",
        "trust_proxy_headers": True,
    }
```

- [ ] **Step 2: Run the Settings tests and verify red**

Run:

```bash
UV_CACHE_DIR=/tmp/short_url_uv_cache uv run pytest -q tests/test_config.py
```

Expected: FAIL because the new fields, parser, validation, and `public_summary()` do not exist.

- [ ] **Step 3: Implement the Settings contract**

Replace `app/config.py` with a typed Settings model using this behavior:

```python
from typing import Literal

from pydantic import ConfigDict, model_validator
from pydantic_settings import BaseSettings


DEV_SECRET_VALUES = {
    "dev-secret",
    "dev-secret-key-change-in-production",
    "dev-secret-key-change-me-in-production",
    "change-me-to-a-random-secret-key-at-least-32-characters",
}


class Settings(BaseSettings):
    model_config = ConfigDict(env_file=".env", extra="ignore")

    app_env: Literal["dev", "prod"] = "dev"
    database_url: str = "postgresql+psycopg://shorturl:shorturl@localhost:5432/shorturl"
    redis_url: str = "redis://localhost:6379/0"
    secret_key: str = "dev-secret-key-change-in-production"
    geoip_db_path: str | None = None
    access_token_expire_minutes: int = 60 * 24
    cookie_secure: bool = False
    cors_origins: str = "http://localhost:3000,http://localhost:5173"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "DEBUG"
    trust_proxy_headers: bool = False

    @property
    def cors_origin_list(self) -> list[str]:
        return [value.strip() for value in self.cors_origins.split(",") if value.strip()]

    @model_validator(mode="after")
    def validate_production(self):
        if self.app_env != "prod":
            return self
        errors = []
        if len(self.secret_key) < 32 or self.secret_key in DEV_SECRET_VALUES:
            errors.append("SECRET_KEY must be at least 32 characters and not a development value")
        if not self.cookie_secure:
            errors.append("COOKIE_SECURE must be true")
        if "*" in self.cors_origin_list:
            errors.append("CORS_ORIGINS must not contain *")
        if not self.database_url.strip():
            errors.append("DATABASE_URL must not be empty")
        if not self.redis_url.strip():
            errors.append("REDIS_URL must not be empty")
        if errors:
            raise ValueError("; ".join(errors))
        return self

    def public_summary(self) -> dict[str, str | bool]:
        return {
            "app_env": self.app_env,
            "cookie_secure": self.cookie_secure,
            "cors_origins": self.cors_origins,
            "log_level": self.log_level,
            "trust_proxy_headers": self.trust_proxy_headers,
        }


settings = Settings()
```

Do not print or return `DATABASE_URL`, `REDIS_URL`, `SECRET_KEY`, account IDs, or license keys from `public_summary()`.

- [ ] **Step 4: Add a config check command**

Create `scripts/check_config.py`:

```python
import json

from app.config import settings


def main() -> None:
    print(json.dumps(settings.public_summary(), ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Expand `.env.example` with the same schema**

Use these non-secret defaults:

```dotenv
APP_ENV=dev
POSTGRES_USER=shorturl
POSTGRES_PASSWORD=shorturl
POSTGRES_DB=shorturl
POSTGRES_PORT=18543
REDIS_PORT=16379
APP_PORT=18000
DATABASE_URL=postgresql+psycopg://shorturl:shorturl@db:5432/shorturl
REDIS_URL=redis://redis:6379/0
SECRET_KEY=change-me-to-a-random-secret-key-at-least-32-characters
GEOIP_DB_PATH=/usr/share/GeoIP/GeoLite2-Country.mmdb
GEOIPUPDATE_ACCOUNT_ID=
GEOIPUPDATE_LICENSE_KEY=
COOKIE_SECURE=false
CORS_ORIGINS=http://localhost:3000,http://localhost:5173
LOG_LEVEL=DEBUG
TRUST_PROXY_HEADERS=false
```

- [ ] **Step 6: Run focused and existing config-adjacent tests**

Run:

```bash
UV_CACHE_DIR=/tmp/short_url_uv_cache uv run pytest -q tests/test_config.py tests/test_auth.py tests/test_main.py
```

Expected: PASS.

- [ ] **Step 7: Verify the command exposes no secrets**

Run:

```bash
UV_CACHE_DIR=/tmp/short_url_uv_cache uv run python scripts/check_config.py
```

Expected: one JSON object containing only `app_env`, `cookie_secure`, `cors_origins`, `log_level`, and `trust_proxy_headers`.

- [ ] **Step 8: Commit Task 1**

```bash
git add app/config.py scripts/check_config.py .env.example tests/test_config.py
git commit -m "feat: validate runtime environment configuration"
```

---

### Task 2: Shared Client IP Resolution and Correct UA Classification

**Files:**
- Create: `app/core/__init__.py`
- Create: `app/core/client_ip.py`
- Modify: `app/main.py:15-31`
- Modify: `app/routers/redirect.py:19-30,84-108`
- Modify: `app/services/ua.py`
- Create: `tests/test_client_ip.py`
- Modify: `tests/test_services_edge.py`
- Modify: `tests/test_redirect.py`

**Interfaces:**
- Consumes: `settings.trust_proxy_headers: bool` from Task 1.
- Produces: `get_client_ip(request: Request, trust_proxy_headers: bool | None = None) -> str`.
- Produces: `get_platform(ua_string: str | None) -> str | None` with Bot-first precedence.
- Produces: interim redirect classification `is_proxy = platform == "bot"`.

- [ ] **Step 1: Write failing client IP tests**

Create `tests/test_client_ip.py`:

```python
from starlette.requests import Request

from app.core.client_ip import get_client_ip


def make_request(client_ip="203.0.113.10", x_real_ip=None):
    headers = []
    if x_real_ip is not None:
        headers.append((b"x-real-ip", x_real_ip.encode()))
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": headers,
            "client": (client_ip, 12345),
            "server": ("testserver", 80),
            "scheme": "http",
            "query_string": b"",
        }
    )


def test_untrusted_proxy_header_is_ignored():
    request = make_request(x_real_ip="198.51.100.7")
    assert get_client_ip(request, trust_proxy_headers=False) == "203.0.113.10"


def test_trusted_proxy_header_is_used():
    request = make_request(x_real_ip="198.51.100.7")
    assert get_client_ip(request, trust_proxy_headers=True) == "198.51.100.7"


def test_invalid_trusted_proxy_header_falls_back_to_socket():
    request = make_request(x_real_ip="not-an-ip")
    assert get_client_ip(request, trust_proxy_headers=True) == "203.0.113.10"


def test_ipv6_is_normalized():
    request = make_request(x_real_ip="2001:0db8:0:0:0:0:0:1")
    assert get_client_ip(request, trust_proxy_headers=True) == "2001:db8::1"
```

- [ ] **Step 2: Add failing Bot-first and redirect policy tests**

Add to `tests/test_services_edge.py`:

```python
def test_bot_detection_precedes_pc_detection(monkeypatch):
    class BotThatLooksLikePc:
        is_bot = True
        is_mobile = False
        is_tablet = False
        is_pc = True

    monkeypatch.setattr("app.services.ua.parse", lambda value: BotThatLooksLikePc())
    assert get_platform("spoofed-bot") == "bot"
```

Add a focused redirect-service test to `tests/test_redirect.py` proving that a Bot fails a rule with `allow_proxy=False` even when `allow_bot=True`; use the existing `_rule()` and `rule_matches()` helpers:

```python
def test_bot_assumed_proxy_requires_proxy_permission():
    rule = _rule(countries=[], ua_platforms=[], allow_bot=True, allow_proxy=False)
    assert not rule_matches(rule, None, "bot", None, is_proxy=True)
```

- [ ] **Step 3: Run the focused tests and verify red**

Run:

```bash
UV_CACHE_DIR=/tmp/short_url_uv_cache uv run pytest -q tests/test_client_ip.py tests/test_services_edge.py::test_bot_detection_precedes_pc_detection tests/test_redirect.py::test_bot_assumed_proxy_requires_proxy_permission
```

Expected: FAIL because `app.core.client_ip` does not exist and Bot classification currently occurs after PC classification.

- [ ] **Step 4: Implement the shared client IP helper**

Create `app/core/__init__.py` as an empty package marker and `app/core/client_ip.py`:

```python
from ipaddress import ip_address

from fastapi import Request

from app.config import settings


def _normalize_ip(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return str(ip_address(value.strip()))
    except ValueError:
        return None


def get_client_ip(
    request: Request,
    trust_proxy_headers: bool | None = None,
) -> str:
    trust_headers = (
        settings.trust_proxy_headers
        if trust_proxy_headers is None
        else trust_proxy_headers
    )
    if trust_headers:
        forwarded_ip = _normalize_ip(request.headers.get("x-real-ip"))
        if forwarded_ip:
            return forwarded_ip
    socket_ip = _normalize_ip(request.client.host if request.client else None)
    return socket_ip or "unknown"
```

Do not parse `X-Forwarded-For` in application code. Nginx owns the one-proxy chain and writes the single canonical address to `X-Real-IP`.

- [ ] **Step 5: Use the helper everywhere client identity is computed**

In `app/main.py`:

- Remove `_default_identifier()`.
- Import `get_client_ip`.
- Return `get_client_ip(request)` as the fallback in `_user_identifier()`.
- Add the async adapter below because fastapi-limiter awaits identifier callbacks.
- Build `ip_rate_limit` with `identifier=_ip_identifier` so redirect limiting and logging use the same address.

```python
async def _ip_identifier(request: Request) -> str:
    return get_client_ip(request)
```

In `app/routers/redirect.py`:

- Remove `_get_client_ip()` and `_is_proxy()`.
- Import and call `get_client_ip(request)`.
- Set `is_proxy = platform == "bot"` before calling `get_redirect_target()`.

This phase intentionally classifies only Bots. Human proxy status stays `False` until the Insights phase introduces a three-state detector.

- [ ] **Step 6: Make UA classification Bot-first**

Update `app/services/ua.py` in this exact order:

```python
def get_platform(ua_string: str | None) -> str | None:
    if not ua_string:
        return None
    ua = parse(ua_string)
    if ua.is_bot:
        return "bot"
    if ua.is_mobile:
        return "mobile"
    if ua.is_tablet:
        return "tablet"
    if ua.is_pc:
        return "pc"
    return "other"
```

- [ ] **Step 7: Run all client identity, UA, redirect, and rate-limit-adjacent tests**

Run:

```bash
UV_CACHE_DIR=/tmp/short_url_uv_cache uv run pytest -q tests/test_client_ip.py tests/test_redirect.py tests/test_services_edge.py tests/test_api.py -k "redirect or proxy or bot or rate or client_ip"
```

Expected: PASS.

- [ ] **Step 8: Commit Task 2**

```bash
git add app/core app/main.py app/routers/redirect.py app/services/ua.py tests/test_client_ip.py tests/test_redirect.py tests/test_services_edge.py
git commit -m "fix: establish trusted client identity boundary"
```

---

### Task 3: Runtime Security Settings and Health Endpoints

**Files:**
- Modify: `app/main.py`
- Modify: `app/routers/auth.py:48-59`
- Modify: `tests/test_main.py`
- Modify: `tests/test_auth.py`

**Interfaces:**
- Consumes: `settings.cors_origin_list`, `settings.cookie_secure`, and `settings.log_level` from Task 1.
- Consumes: `get_client_ip` from Task 2 through rate limiting.
- Produces: `GET /health/live -> {"status": "ok"}`.
- Produces: `GET /health/ready -> {"status": "ready", "checks": {...}}` or HTTP 503.
- Produces: `app.state.redis` during lifespan.

- [ ] **Step 1: Write failing runtime tests**

Replace the single health test in `tests/test_main.py` with:

```python
async def test_liveness(client):
    response = await client.get("/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_readiness(client):
    response = await client.get("/health/ready")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert body["checks"]["database"] == "ok"
    assert body["checks"]["redis"] in {"ok", "disabled"}


async def test_legacy_health_remains_compatible(client):
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
```

Add to `tests/test_auth.py` a direct assertion around the configured cookie policy by monkeypatching the shared Settings singleton and calling the existing `/api/auth/login-cookie` flow:

```python
async def test_login_cookie_uses_configured_secure_flag(client, monkeypatch):
    monkeypatch.setattr("app.routers.auth.settings.cookie_secure", True)
    response = await client.post(
        "/api/auth/login-cookie",
        data={"username": "admin", "password": "admin123"},
    )
    assert response.status_code == 200
    assert "Secure" in response.headers["set-cookie"]
```

- [ ] **Step 2: Run the runtime tests and verify red**

Run:

```bash
UV_CACHE_DIR=/tmp/short_url_uv_cache uv run pytest -q tests/test_main.py tests/test_auth.py
```

Expected: FAIL because `/health/live`, `/health/ready`, and configurable secure cookies do not exist.

- [ ] **Step 3: Configure logging, CORS, and Redis application state**

In `app/main.py`:

- Call `logging.basicConfig(level=settings.log_level)` before app creation.
- Store the initialized Redis connection as `app.state.redis`.
- Set `app.state.redis = None` when Redis is disabled.
- Clear `app.state.redis` during shutdown.
- Replace `allow_origins=["*"]` with `allow_origins=settings.cors_origin_list`.
- Keep `allow_credentials=True`, `allow_methods=["*"]`, and `allow_headers=["*"]`.

- [ ] **Step 4: Implement live and ready health endpoints**

Use SQLAlchemy `text("SELECT 1")`, `AsyncSessionLocal`, and `app.state.redis`:

```python
@app.get("/health/live")
@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/health/ready")
async def readiness(request: Request):
    checks = {}
    try:
        async with AsyncSessionLocal() as session:
            await session.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception:
        checks["database"] = "error"

    redis_client = request.app.state.redis
    if redis_client is None:
        checks["redis"] = "disabled"
    else:
        try:
            await redis_client.ping()
            checks["redis"] = "ok"
        except Exception:
            checks["redis"] = "error"

    if "error" in checks.values():
        return JSONResponse(
            status_code=503,
            content={"status": "not_ready", "checks": checks},
        )
    return {"status": "ready", "checks": checks}
```

Log exceptions with `logger.exception()` before returning 503; do not expose exception text in the response.

- [ ] **Step 5: Configure the authentication cookie**

In `app/routers/auth.py`, change only:

```python
secure=settings.cookie_secure,
```

Keep `httponly=True`, `samesite="lax"`, and the existing max age.

- [ ] **Step 6: Run runtime and authentication tests**

Run:

```bash
UV_CACHE_DIR=/tmp/short_url_uv_cache uv run pytest -q tests/test_main.py tests/test_auth.py tests/test_api.py -k "login or health"
```

Expected: PASS.

- [ ] **Step 7: Commit Task 3**

```bash
git add app/main.py app/routers/auth.py tests/test_main.py tests/test_auth.py
git commit -m "feat: configure runtime security and readiness"
```

---

### Task 4: One-Compose and Makefile Operator Workflow

**Files:**
- Modify: `docker-compose.yml`
- Modify: `Makefile`
- Modify: `.env.example`
- Create: `tests/test_deployment_config.py`

**Interfaces:**
- Consumes: `scripts/check_config.py` from Task 1.
- Produces: Make targets `env`, `check-config`, `up`, `down`, `logs`, `restart`, `migrate`, and existing test targets.
- Produces: Compose services reachable only through `127.0.0.1` host bindings.

- [ ] **Step 1: Write failing repository configuration contract tests**

Create `tests/test_deployment_config.py`:

```python
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def env_keys():
    keys = set()
    for line in (ROOT / ".env.example").read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            keys.add(line.split("=", 1)[0])
    return keys


def test_env_example_contains_runtime_and_compose_contract():
    assert {
        "APP_ENV",
        "POSTGRES_USER",
        "POSTGRES_PASSWORD",
        "POSTGRES_DB",
        "POSTGRES_PORT",
        "REDIS_PORT",
        "APP_PORT",
        "DATABASE_URL",
        "REDIS_URL",
        "SECRET_KEY",
        "COOKIE_SECURE",
        "CORS_ORIGINS",
        "LOG_LEVEL",
        "TRUST_PROXY_HEADERS",
    } <= env_keys()


def test_compose_ports_are_loopback_only():
    compose = (ROOT / "docker-compose.yml").read_text()
    assert '"127.0.0.1:${POSTGRES_PORT:-18543}:5432"' in compose
    assert '"127.0.0.1:${REDIS_PORT:-16379}:6379"' in compose
    assert '"127.0.0.1:${APP_PORT:-18000}:8000"' in compose
    assert "postgresql+asyncpg" not in compose


def test_makefile_has_one_environment_agnostic_entrypoint():
    makefile = (ROOT / "Makefile").read_text()
    assert "env:" in makefile
    assert "check-config:" in makefile
    assert "up: check-config" in makefile
    assert "dev-up:" not in makefile
    assert "prod-up:" not in makefile
```

- [ ] **Step 2: Run configuration contract tests and verify red**

Run:

```bash
UV_CACHE_DIR=/tmp/short_url_uv_cache uv run pytest -q tests/test_deployment_config.py
```

Expected: FAIL because loopback bindings and Make targets are absent.

- [ ] **Step 3: Parameterize and harden Docker Compose**

Update `docker-compose.yml` with these exact contracts:

```yaml
services:
  db:
    environment:
      POSTGRES_USER: ${POSTGRES_USER:-shorturl}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:-shorturl}
      POSTGRES_DB: ${POSTGRES_DB:-shorturl}
    ports:
      - "127.0.0.1:${POSTGRES_PORT:-18543}:5432"
    restart: unless-stopped

  redis:
    ports:
      - "127.0.0.1:${REDIS_PORT:-16379}:6379"
    restart: unless-stopped

  app:
    environment:
      APP_ENV: ${APP_ENV:-dev}
      DATABASE_URL: ${DATABASE_URL:-postgresql+psycopg://shorturl:shorturl@db:5432/shorturl}
      REDIS_URL: ${REDIS_URL:-redis://redis:6379/0}
      SECRET_KEY: ${SECRET_KEY}
      GEOIP_DB_PATH: ${GEOIP_DB_PATH:-/usr/share/GeoIP/GeoLite2-Country.mmdb}
      COOKIE_SECURE: ${COOKIE_SECURE:-false}
      CORS_ORIGINS: ${CORS_ORIGINS:-http://localhost:3000,http://localhost:5173}
      LOG_LEVEL: ${LOG_LEVEL:-DEBUG}
      TRUST_PROXY_HEADERS: ${TRUST_PROXY_HEADERS:-false}
    ports:
      - "127.0.0.1:${APP_PORT:-18000}:8000"
    restart: unless-stopped
    healthcheck:
      test:
        - CMD
        - python
        - -c
        - "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/ready', timeout=3)"
      interval: 10s
      timeout: 5s
      retries: 5
      start_period: 10s
```

Preserve current volume names, network name, dependencies, GeoIP profile, and published container ports.

- [ ] **Step 4: Make Makefile the single operator interface**

Add `env` and `check-config`, and make `up` depend on validation:

```makefile
.PHONY: env check-config

env:
	@$(DOCKER_COMPOSE) run --rm --no-deps app python scripts/check_config.py

check-config:
	@$(DOCKER_COMPOSE) config --quiet
	@$(DOCKER_COMPOSE) run --rm --build --no-deps app python scripts/check_config.py

up: check-config
	$(DOCKER_COMPOSE) up -d
```

Do not add environment-specific Make targets. Keep existing migration, initialization, test, and GeoIP targets.

Change `migrate` to execute migrations from a one-off container created from the newly built image, rather than from a possibly stale running app container:

```makefile
migrate:
	$(DOCKER_COMPOSE) run --rm app alembic upgrade head
```

- [ ] **Step 5: Run tests and render Compose configuration**

Run:

```bash
UV_CACHE_DIR=/tmp/short_url_uv_cache uv run pytest -q tests/test_deployment_config.py tests/test_config.py
docker compose --env-file .env.example config --quiet
make -n up
```

Expected: all tests pass, Compose exits 0, and the dry run shows config validation before `docker compose up -d`.

- [ ] **Step 6: Build and start the development stack without recreating volumes**

Run:

```bash
make up
docker compose ps
```

Expected: `db`, `redis`, and `app` are healthy; existing named volumes remain attached.

- [ ] **Step 7: Commit Task 4**

```bash
git add docker-compose.yml Makefile .env.example tests/test_deployment_config.py
git commit -m "build: unify dev and prod compose workflow"
```

---

### Task 5: Deployment Runbook and Phase Verification

**Files:**
- Create: `docs/deployment.md`
- Create: `tests/test_deployment_docs.py`
- Verify: all files changed by Tasks 1-4

**Interfaces:**
- Consumes: Make targets and `.env` schema from Tasks 1 and 4.
- Consumes: `/health/live` and `/health/ready` from Task 3.
- Produces: the exact Nginx and manual Ubuntu deployment procedure used by operators.

- [ ] **Step 1: Write failing documentation contract tests**

Create `tests/test_deployment_docs.py`:

```python
from pathlib import Path


DOC = Path(__file__).resolve().parents[1] / "docs" / "deployment.md"


def test_nginx_overwrites_single_proxy_headers():
    text = DOC.read_text()
    assert "proxy_pass http://127.0.0.1:18000;" in text
    assert "proxy_set_header Host $host;" in text
    assert "proxy_set_header X-Real-IP $remote_addr;" in text
    assert "proxy_set_header X-Forwarded-For $remote_addr;" in text
    assert "proxy_set_header X-Forwarded-Proto $scheme;" in text
    assert "$proxy_add_x_forwarded_for" not in text


def test_runbook_uses_makefile_entrypoints():
    text = DOC.read_text()
    for command in (
        "make check-config",
        "make up",
        "make migrate",
        "make logs",
        "make restart",
    ):
        assert command in text
```

- [ ] **Step 2: Run documentation tests and verify red**

Run:

```bash
UV_CACHE_DIR=/tmp/short_url_uv_cache uv run pytest -q tests/test_deployment_docs.py
```

Expected: FAIL because `docs/deployment.md` does not exist.

- [ ] **Step 3: Write the deployment runbook**

Create `docs/deployment.md` with these sections and exact operational contracts:

1. Prerequisites: Ubuntu, Docker Engine with Compose plugin, Git, existing Nginx, DNS, and TLS certificate.
2. One `.env` per machine copied from `.env.example`; never copy the development `.env` to production.
3. Production values: `APP_ENV=prod`, a 32+ character non-default secret, `COOKIE_SECURE=true`, explicit HTTPS CORS origins, and `TRUST_PROXY_HEADERS=true`.
4. First deployment: `make check-config`, `make up`, `make migrate`, `make create-domain`, and `make create-admin`.
5. Nginx block using `$remote_addr` for both real-IP headers and `127.0.0.1:18000` upstream.
6. Verification: `/health/live`, `/health/ready`, `make logs`, and an actual short-link request that verifies Host, IP, UA, and Referer logging.
7. Update: pull the chosen commit, back up PostgreSQL, run `make build`, `make migrate`, and `make up` so Compose recreates containers from the new image.
8. Rollback: restore the previous application commit/image; do not downgrade the database unless that release's migration procedure explicitly supports it.

- [ ] **Step 4: Run documentation and focused phase tests**

Run:

```bash
UV_CACHE_DIR=/tmp/short_url_uv_cache uv run pytest -q tests/test_deployment_docs.py tests/test_deployment_config.py tests/test_config.py tests/test_client_ip.py tests/test_main.py tests/test_auth.py tests/test_redirect.py tests/test_services_edge.py
```

Expected: PASS.

- [ ] **Step 5: Run complete verification**

Run:

```bash
UV_CACHE_DIR=/tmp/short_url_uv_cache uv run pytest -q
.venv/bin/python -m compileall -q app tests scripts alembic
docker compose --env-file .env.example config --quiet
docker compose ps
curl --fail --silent http://127.0.0.1:18000/health/live
curl --fail --silent http://127.0.0.1:18000/health/ready
git diff --check
```

Expected:

- all pytest tests pass
- compileall exits 0
- Compose config exits 0
- `db`, `redis`, and `app` report healthy
- both health requests return HTTP 200
- `git diff --check` exits 0

- [ ] **Step 6: Commit Task 5**

```bash
git add docs/deployment.md tests/test_deployment_docs.py
git commit -m "docs: add manual production deployment runbook"
```

---

## Phase 1 Completion Gate

Phase 1 is complete only when:

- all five task commits exist
- the full test suite passes against PostgreSQL
- Compose renders from `.env.example`
- all published service ports bind to `127.0.0.1`
- FastAPI uses the configured CORS and cookie settings
- logging and rate limiting resolve the same validated client IP
- forwarding headers no longer imply end-user proxy usage
- Bots are classified before PCs and treated as proxies without a MaxMind query
- live and ready health endpoints return 200 on the running stack
- the deployment runbook contains the approved one-Nginx header configuration

After this gate, create the separate phase 2 plan for the feature-oriented directory refactor. Do not combine phase 2 file moves with phase 1 behavior changes.
