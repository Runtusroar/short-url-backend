# Short URL Production Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Publish the verified local backend and frontend as the authoritative GitHub `main` branches, then migrate the live service and all retained PostgreSQL data from `47.236.81.129` to `47.236.145.202` with tested compatibility, TLS, real-client-IP handling, and rollback assets.

**Architecture:** The target runs the backend, PostgreSQL, Redis, GeoIP updater, and frontend with Docker Compose, while the existing aaPanel Nginx remains the only public listener. A rehearsal proves the forward-only Alembic migration against a source dump before a short final write freeze; the sole incompatible legacy Facebook Referer rule is converted with exact, guarded SQL on restored copies only.

**Tech Stack:** Git/GitHub, Docker Engine, Docker Compose v2, PostgreSQL 15, Redis 7, FastAPI, Alembic, React/Vite, Nginx, Certbot, Cloudflare real-IP headers.

**Spec:** `docs/superpowers/specs/2026-09-12-production-migration-design.md`

## Global Constraints

- Source server: `47.236.81.129`; its application directory, PostgreSQL volume, and backups remain intact.
- Target server: `47.236.145.202`; deploy only below `/root/docker` and the two new aaPanel virtual-host files.
- Backend GitHub repository: `Runtusroar/short-url-backend`; expected old remote `main`: `9c4bcf58da8e9a8802a6fee381ccc56e3b828fba`.
- Frontend GitHub repository: `Runtusroar/short-url-frontend`; expected old remote `main`: `4fa6b70789767512b8476d2a36655908bb18645a`.
- Short-link domain: `juezhou.top`; administration domain: `shotcc.shop`.
- Target paths: `/root/docker/short-url-backend` and `/root/docker/short-url-frontend`.
- All host-published application, frontend, PostgreSQL, and Redis ports bind explicitly to `127.0.0.1`; UFW does not expose them.
- Production uses `APP_ENV=production`, `PUBLIC_SHORT_URL_SCHEME=https`, an empty `PUBLIC_SHORT_URL_PORT`, `COOKIE_SECURE=true`, and `TRUST_PROXY_HEADERS=true`.
- Secret values are never committed, printed in command output, or copied into reports.
- The source schema revision must be `a9e56b03bf5f`; the target revision after migration must be `20260912_refactor`.
- Legacy link `4b9cfc0c-1b7d-4515-8071-0cb009277d3b` (`US3tHH`, note `G1`) becomes country allow `DE`, Referer allow `facebook.com` plus `*.facebook.com`, proxy blocked, and bot blocked.
- Existing target Nginx sites `indcc.top` and `zhenmei.shop` are not modified.
- Every destructive or replacement command must first validate its exact target and expected state in the same foreground shell.

## File Structure

### Backend repository

- Modify `docker-compose.yml`: make database credentials configurable and bind every published port to loopback.
- Modify `.env.example`: document the Compose PostgreSQL variables used in production.
- Modify `tests/test_compose_contract.py`: assert the resolved Compose contract for credentials and host bindings.
- Create `docs/superpowers/plans/2026-09-12-production-migration.md`: this execution plan.

### Frontend repository

- Modify `docker-compose.yml`: bind port `18080` to loopback.
- Create `tests/compose-contract.test.ts`: assert the resolved frontend Compose host binding.

### Target server runtime files (not committed)

- `/root/docker/short-url-backend/.env`: root-readable production environment.
- `/root/docker/migration-backups/`: dumps, checksums, count snapshots, and policy snapshots.
- `/www/wwwroot/short-url-acme/`: ACME HTTP challenge webroot.
- `/www/server/panel/vhost/nginx/cloudflare-realip-short-url.conf`: generated trusted Cloudflare network directives.
- `/www/server/panel/vhost/nginx/juezhou.top.conf`: HTTPS short-link proxy.
- `/www/server/panel/vhost/nginx/shotcc.shop.conf`: HTTPS frontend and direct `/api/` backend proxy.

---

### Task 1: Make checked-in Compose defaults safe for production

**Files:**
- Modify: `docker-compose.yml`
- Modify: `.env.example`
- Modify: `tests/test_compose_contract.py`
- Modify: `../short_url_frontend/docker-compose.yml`
- Create: `../short_url_frontend/tests/compose-contract.test.ts`

**Interfaces:**
- Consumes: Docker Compose v2 JSON output from `docker compose config --format json`.
- Produces: configurable `POSTGRES_USER`, `POSTGRES_PASSWORD`, and `POSTGRES_DB`; loopback-only published ports `18543`, `16379`, `18000`, and `18080`.

- [ ] **Step 1: Add failing backend Compose contract tests**

Extend `tests/test_compose_contract.py` with helpers that resolve Compose JSON and assert the exact port and credential contract:

```python
def _compose_config(environment: dict[str, str]) -> dict:
    project_root = Path(__file__).resolve().parents[1]
    configured = subprocess.run(
        ["docker", "compose", "config", "--format", "json"],
        cwd=project_root,
        env=os.environ | environment,
        check=False,
        capture_output=True,
        text=True,
    )
    assert configured.returncode == 0, configured.stderr
    return json.loads(configured.stdout)


def test_compose_binds_all_published_backend_ports_to_loopback():
    config = _compose_config({"SECRET_KEY": "development-secret-key"})
    expected = {
        "db": ("127.0.0.1", 18543, 5432),
        "redis": ("127.0.0.1", 16379, 6379),
        "app": ("127.0.0.1", 18000, 8000),
    }
    for service, binding in expected.items():
        ports = config["services"][service]["ports"]
        assert [(port["host_ip"], int(port["published"]), port["target"]) for port in ports] == [binding]


def test_compose_uses_configured_postgres_credentials_consistently():
    config = _compose_config(
        {
            "SECRET_KEY": "development-secret-key",
            "POSTGRES_USER": "configured_user",
            "POSTGRES_PASSWORD": "configured_password",
            "POSTGRES_DB": "configured_db",
        }
    )
    database = config["services"]["db"]
    assert database["environment"] == {
        "POSTGRES_DB": "configured_db",
        "POSTGRES_PASSWORD": "configured_password",
        "POSTGRES_USER": "configured_user",
    }
    assert "pg_isready -U $${POSTGRES_USER} -d $${POSTGRES_DB}" in database["healthcheck"]["test"][-1]
```

- [ ] **Step 2: Run the backend contract tests and verify the new assertions fail**

Run: `uv run pytest tests/test_compose_contract.py -q`

Expected: the loopback test fails for `db` and `redis`, and the credential test fails because their values are hard-coded.

- [ ] **Step 3: Update the backend Compose contract minimally**

Change `docker-compose.yml` to:

```yaml
services:
  db:
    environment:
      POSTGRES_USER: ${POSTGRES_USER:-shorturl}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:-shorturl}
      POSTGRES_DB: ${POSTGRES_DB:-shorturl}
    ports:
      - "127.0.0.1:18543:5432"
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U $${POSTGRES_USER} -d $${POSTGRES_DB}"]

  redis:
    ports:
      - "127.0.0.1:16379:6379"
```

Keep the existing app binding `127.0.0.1:18000:8000` unchanged.

Add the development-safe defaults to `.env.example` immediately above `DATABASE_URL`:

```dotenv
POSTGRES_USER=shorturl
POSTGRES_PASSWORD=shorturl
POSTGRES_DB=shorturl
```

- [ ] **Step 4: Run the backend contract tests and full backend suite**

Run: `uv run pytest tests/test_compose_contract.py -q`

Expected: PASS.

Run: `uv run pytest -q`

Expected: all backend tests pass.

- [ ] **Step 5: Commit the backend hardening**

```bash
git add docker-compose.yml .env.example tests/test_compose_contract.py
git commit -m "chore: bind compose services to loopback"
```

- [ ] **Step 6: Add the failing frontend Compose contract test**

Create `../short_url_frontend/tests/compose-contract.test.ts`:

```typescript
import { execFileSync } from 'node:child_process';
import { describe, expect, it } from 'vitest';

interface PortBinding {
  host_ip: string;
  published: string;
  target: number;
}

describe('frontend Compose contract', () => {
  it('publishes the frontend only on loopback', () => {
    const output = execFileSync('docker', ['compose', 'config', '--format', 'json'], {
      encoding: 'utf8',
    });
    const config = JSON.parse(output) as {
      services: { frontend: { ports: PortBinding[] } };
    };

    expect(config.services.frontend.ports).toEqual([
      { mode: 'ingress', target: 80, published: '18080', protocol: 'tcp', host_ip: '127.0.0.1' },
    ]);
  });
});
```

- [ ] **Step 7: Run the frontend test and verify it fails**

Run from `../short_url_frontend`: `pnpm test tests/compose-contract.test.ts`

Expected: FAIL because `host_ip` is `0.0.0.0` or absent.

- [ ] **Step 8: Bind the frontend port to loopback**

Change `../short_url_frontend/docker-compose.yml` to:

```yaml
ports:
  - "127.0.0.1:18080:80"
```

- [ ] **Step 9: Run the frontend contract and full frontend checks**

Run from `../short_url_frontend`:

```bash
pnpm test
pnpm type-check
pnpm lint
pnpm build
```

Expected: all commands succeed.

- [ ] **Step 10: Commit the frontend hardening**

```bash
git add docker-compose.yml tests/compose-contract.test.ts
git commit -m "chore: bind frontend to loopback"
```

### Task 2: Verify and replace both GitHub `main` branches

**Files:**
- Verify only: both repositories and their generated test/build outputs.

**Interfaces:**
- Consumes: expected old remote SHAs from Global Constraints and clean verified local `main` branches.
- Produces: GitHub `main` refs equal to the final local backend and frontend SHAs.

- [ ] **Step 1: Verify the backend working tree and complete test suite**

Run from the backend repository:

```bash
test "$(git branch --show-current)" = main
test -z "$(git status --porcelain)"
uv run pytest -q
docker compose config --quiet
```

Expected: all checks succeed and the tree is clean.

- [ ] **Step 2: Verify the frontend working tree and complete quality suite**

Run from the frontend repository:

```bash
test "$(git branch --show-current)" = main
test -z "$(git status --porcelain)"
pnpm test
pnpm type-check
pnpm lint
pnpm build
docker compose config --quiet
```

Expected: all checks succeed and the tree is clean after ignored build output.

- [ ] **Step 3: Replace backend remote `main` with an exact lease**

Run as one foreground shell from the backend repository:

```bash
EXPECTED_REMOTE=9c4bcf58da8e9a8802a6fee381ccc56e3b828fba
test -n "${EXPECTED_REMOTE:?}"
git fetch origin main
test "$(git rev-parse refs/remotes/origin/main)" = "$EXPECTED_REMOTE"
LOCAL_HEAD=$(git rev-parse HEAD)
test -n "${LOCAL_HEAD:?}"
git push origin "HEAD:refs/heads/main" --force-with-lease="refs/heads/main:${EXPECTED_REMOTE}"
git fetch origin main
test "$(git rev-parse refs/remotes/origin/main)" = "$LOCAL_HEAD"
```

Expected: the push succeeds only if nobody changed the old remote branch.

- [ ] **Step 4: Replace frontend remote `main` with an exact lease**

Run as one foreground shell from the frontend repository:

```bash
EXPECTED_REMOTE=4fa6b70789767512b8476d2a36655908bb18645a
test -n "${EXPECTED_REMOTE:?}"
git fetch origin main
test "$(git rev-parse refs/remotes/origin/main)" = "$EXPECTED_REMOTE"
LOCAL_HEAD=$(git rev-parse HEAD)
test -n "${LOCAL_HEAD:?}"
git push origin "HEAD:refs/heads/main" --force-with-lease="refs/heads/main:${EXPECTED_REMOTE}"
git fetch origin main
test "$(git rev-parse refs/remotes/origin/main)" = "$LOCAL_HEAD"
```

Expected: the push succeeds only if nobody changed the old remote branch.

### Task 3: Capture the source and rehearse the migration locally

**Files:**
- Create outside Git: `/Users/wangcaixian/Documents/kimi/short_url_migration_backups/${MIGRATION_STAMP}/source-rehearsal.dump`
- Create outside Git: matching `.sha256`, count, and policy snapshot files.

**Interfaces:**
- Consumes: live source PostgreSQL at revision `a9e56b03bf5f` and the newly published backend code.
- Produces: a verified source dump and proof that restore, policy conversion, Alembic upgrade, and UA backfill succeed without touching source data.

- [ ] **Step 1: Record source identity, revision, counts, and the incompatible policy without exposing secrets**

In the existing source SSH session, set `MIGRATION_STAMP=$(date -u +%Y%m%dT%H%M%SZ)`, require it to match `^[0-9]{8}T[0-9]{6}Z$`, and save the read-only results below `/root/migration-backups/${MIGRATION_STAMP}/`. The SQL must record:

```sql
SELECT version_num FROM alembic_version;
SELECT 'users', count(*) FROM users
UNION ALL SELECT 'domains', count(*) FROM domains
UNION ALL SELECT 'short_links', count(*) FROM short_links
UNION ALL SELECT 'target_urls', count(*) FROM target_urls
UNION ALL SELECT 'access_logs', count(*) FROM access_logs
UNION ALL SELECT 'ip_blacklist', count(*) FROM ip_blacklist;
SELECT short_link_id, action, priority, countries, ua_platforms,
       referer_pattern, allow_proxy, allow_bot, is_active
FROM access_rules
WHERE short_link_id = '4b9cfc0c-1b7d-4515-8071-0cb009277d3b';
```

Expected: revision `a9e56b03bf5f`; the Facebook rule still has `*facebook*`, country `DE`, `allow_proxy=false`, and `allow_bot=false`.

- [ ] **Step 2: Create and checksum a custom-format rehearsal dump on the source**

In one source-server shell, create a mode-700 timestamped directory, validate it is below `/root/migration-backups`, dump the `shorturl` database with `pg_dump -Fc --no-owner --no-acl`, and run `sha256sum`. Keep the app running during this rehearsal snapshot.

Expected: `pg_restore --list` can read the dump and both dump and checksum files are non-empty.

- [ ] **Step 3: Transfer the rehearsal dump through the operator machine**

Create the exact local parent directory `/Users/wangcaixian/Documents/kimi/short_url_migration_backups`, copy the dump and checksum with `scp`, and run `sha256sum -c` locally.

Expected: checksum verification reports `OK`; no `.env` content is printed.

- [ ] **Step 4: Start an isolated PostgreSQL 15 rehearsal container**

Use container name `short-url-migration-rehearsal` and host binding `127.0.0.1:25432`. Before creation, inspect `docker ps -a --filter name=^/short-url-migration-rehearsal$`; stop if any container already owns that exact name. Start `postgres:15-alpine` with database/user `shorturl` and a rehearsal-only password, then wait for `pg_isready`.

Expected: the new empty database accepts connections only through loopback.

- [ ] **Step 5: Restore the dump and apply the guarded pre-migration mapping**

Restore with `pg_restore --exit-on-error --no-owner --no-acl`. Then run this SQL against the rehearsal database:

```sql
DO $$
DECLARE changed integer;
BEGIN
  UPDATE access_rules
  SET referer_pattern = NULL
  WHERE short_link_id = '4b9cfc0c-1b7d-4515-8071-0cb009277d3b'
    AND referer_pattern = '*facebook*'
    AND countries = '["DE"]'::jsonb
    AND allow_proxy = false
    AND allow_bot = false;
  GET DIAGNOSTICS changed = ROW_COUNT;
  IF changed <> 1 THEN
    RAISE EXCEPTION 'Expected one Facebook legacy rule, changed %', changed;
  END IF;
END $$;
```

Expected: one and only one legacy row changes in the rehearsal copy.

- [ ] **Step 6: Upgrade and apply the guarded post-migration policy**

From the backend repository, run Alembic against `postgresql+psycopg://shorturl:rehearsal@127.0.0.1:25432/shorturl`. After the upgrade, execute:

```sql
DO $$
DECLARE changed integer;
BEGIN
  UPDATE link_policies
  SET referer_mode = 'allow',
      referer_patterns = '["facebook.com", "*.facebook.com"]'::jsonb,
      updated_at = now()
  WHERE short_link_id = '4b9cfc0c-1b7d-4515-8071-0cb009277d3b'
    AND country_mode = 'allow'
    AND countries = '["DE"]'::jsonb
    AND block_proxy = true
    AND block_bot = true
    AND referer_mode = 'off'
    AND referer_patterns = '[]'::jsonb;
  GET DIAGNOSTICS changed = ROW_COUNT;
  IF changed <> 1 THEN
    RAISE EXCEPTION 'Expected one migrated Facebook policy, changed %', changed;
  END IF;
END $$;
```

Expected: Alembic reports `20260912_refactor` and exactly one policy is updated.

- [ ] **Step 7: Backfill user agents and verify rehearsal invariants**

Run `uv run python scripts/backfill_user_agents.py --batch-size 500` against the rehearsal URL. Verify:

```sql
SELECT version_num FROM alembic_version;
SELECT count(*) FROM access_logs WHERE ua_raw IS NOT NULL AND ua_browser IS NULL;
SELECT country_mode, countries, referer_mode, referer_patterns, block_proxy, block_bot
FROM link_policies
WHERE short_link_id = '4b9cfc0c-1b7d-4515-8071-0cb009277d3b';
```

Compare all preserved-table counts and the nine short-link IDs/codes with the source snapshot.

Expected: revision is `20260912_refactor`, pending UA count is zero, counts match, and the Facebook policy exactly matches Global Constraints.

- [ ] **Step 8: Preserve rehearsal evidence and remove only the disposable container**

Export final rehearsal counts and policy to the local timestamped backup directory. Validate the exact container name and that its image is `postgres:15-alpine`, then remove only `short-url-migration-rehearsal` with `docker rm -f short-url-migration-rehearsal`.

Expected: the source service is still running and all dump/evidence files remain.

### Task 4: Prepare the target Docker runtime and production configuration

**Files:**
- Create: `/root/docker/short-url-backend/`
- Create: `/root/docker/short-url-frontend/`
- Create: `/root/docker/short-url-backend/.env`
- Create: `/root/docker/migration-backups/`

**Interfaces:**
- Consumes: the newly published GitHub repositories and preserved source application credentials.
- Produces: an installed Docker runtime, exact clean checkouts, a root-readable production environment, and healthy empty PostgreSQL/Redis services.

- [ ] **Step 1: Reconfirm target identity and available capacity**

On `47.236.145.202`, verify Ubuntu 24.04, at least 20 GB free under `/root`, and that `/root/docker` is absent or contains no conflicting short-url directories. List running containers and aaPanel virtual hosts before any mutation.

Expected: no existing short-url target containers, volumes, directories, or virtual hosts conflict.

- [ ] **Step 2: Install standard Ubuntu Docker and Certbot packages**

Run `apt-get update`, then install `docker.io`, `docker-compose-v2`, and `certbot`. Enable and start Docker, then verify `docker version`, `docker compose version`, and `certbot --version`.

Expected: all three commands succeed; aaPanel Nginx remains running and owns ports 80/443.

- [ ] **Step 3: Create exact deployment and backup directories**

Create `/root/docker` and `/root/docker/migration-backups` with owner `root:root` and mode `700`. Validate both resolved paths begin with `/root/docker/` before writing backup data.

- [ ] **Step 4: Clone and pin the published repositories**

Clone the backend into `/root/docker/short-url-backend` and frontend into `/root/docker/short-url-frontend`. Fetch `origin/main`, check out `main`, and verify each `HEAD` equals the GitHub SHA recorded after Task 2.

Expected: both working trees are clean and their Compose files contain loopback bindings.

- [ ] **Step 5: Transfer the source environment privately and convert it to production**

Copy the source backend `.env` through the operator machine without displaying it. On the target, set mode `600`, generate a 64-character hexadecimal PostgreSQL password with `openssl rand -hex 32`, and rewrite/add these exact keys while preserving the existing application secret and MaxMind credentials:

```dotenv
APP_ENV=production
POSTGRES_USER=shorturl
POSTGRES_PASSWORD=${DB_PASSWORD}
POSTGRES_DB=shorturl
DATABASE_URL=postgresql+psycopg://shorturl:${DB_PASSWORD}@db:5432/shorturl
REDIS_URL=redis://redis:6379/0
APP_TIMEZONE=Asia/Shanghai
PUBLIC_SHORT_URL_SCHEME=https
PUBLIC_SHORT_URL_PORT=
COOKIE_SECURE=true
TRUST_PROXY_HEADERS=true
IP_REPUTATION_TTL_HOURS=168
MAXMIND_TIMEOUT_SECONDS=1.5
```

The generated value is substituted locally on the target and is never echoed. Copy `GEOIPUPDATE_ACCOUNT_ID`/`GEOIPUPDATE_LICENSE_KEY` into `MAXMIND_ACCOUNT_ID`/`MAXMIND_LICENSE_KEY` only if the latter keys are absent, again without printing either value. Validate key presence and secret lengths, not values.

- [ ] **Step 6: Validate resolved Compose without printing the environment**

Run `docker compose config --quiet` from the backend and frontend directories. Use JSON parsing limited to service names, image/build fields, networks, and port host addresses; do not print resolved environment entries.

Expected: PostgreSQL, Redis, FastAPI, and frontend bind only to `127.0.0.1`; network name is `short_url_network`.

- [ ] **Step 7: Build images and start only PostgreSQL and Redis**

From the backend directory, run `docker compose build app` and `docker compose up -d db redis`. Wait for both health checks.

Expected: database and Redis are healthy; app and frontend are not yet serving migrated traffic.

### Task 5: Install target Nginx real-IP handling and obtain fresh TLS certificates

**Files:**
- Create: `/www/server/panel/vhost/nginx/cloudflare-realip-short-url.conf`
- Create: `/www/server/panel/vhost/nginx/juezhou.top.conf`
- Create: `/www/server/panel/vhost/nginx/shotcc.shop.conf`
- Create: `/www/wwwroot/short-url-acme/`

**Interfaces:**
- Consumes: Cloudflare's current official IPv4/IPv6 lists and DNS already pointing both domains at the target origin.
- Produces: trusted client-IP normalization, fresh certificates, HTTP-to-HTTPS redirects, and reverse-proxy routes ready for the target containers.

- [ ] **Step 1: Back up only colliding target virtual-host files**

Inspect the three exact destination paths. If any exists, copy it to `/root/docker/migration-backups/nginx-before-short-url/` with metadata preserved. Do not touch `indcc.top`, `zhenmei.shop`, or any shared aaPanel file.

- [ ] **Step 2: Generate the Cloudflare trust include from official current ranges**

Download `https://www.cloudflare.com/ips-v4` and `https://www.cloudflare.com/ips-v6` with `curl --fail --silent --show-error`. Validate every non-empty line as a CIDR, require at least one IPv4 and one IPv6 range, then generate:

```nginx
set_real_ip_from 173.245.48.0/20;
real_ip_header CF-Connecting-IP;
real_ip_recursive on;
```

Write the result to `cloudflare-realip-short-url.conf` with mode `644`. No `0.0.0.0/0` or `::/0` entry is permitted.

- [ ] **Step 3: Install HTTP-only challenge virtual hosts**

Create `/www/wwwroot/short-url-acme/.well-known/acme-challenge/` and initial port-80 server blocks for `juezhou.top` and `shotcc.shop` that serve only the challenge path from that webroot and return `503` for other paths during preparation.

Run `/www/server/nginx/sbin/nginx -t` and reload with `/www/server/nginx/sbin/nginx -s reload` only after a successful test.

- [ ] **Step 4: Obtain separate fresh certificates**

Run Certbot webroot issuance with `--non-interactive --agree-tos --register-unsafely-without-email` for `juezhou.top`, then for `shotcc.shop`. Do not copy the expired source certificate.

Expected: `/etc/letsencrypt/live/juezhou.top/fullchain.pem` and `/etc/letsencrypt/live/shotcc.shop/fullchain.pem` exist and `certbot certificates` shows valid dates.

- [ ] **Step 5: Install final HTTPS proxy virtual hosts**

Configure `juezhou.top` HTTPS to include the Cloudflare real-IP file and proxy every path to `http://127.0.0.1:18000`. Configure `shotcc.shop` HTTPS so `/api/` proxies directly to `http://127.0.0.1:18000` and all other paths proxy to `http://127.0.0.1:18080`. Every proxy location sets:

```nginx
proxy_set_header Host $host;
proxy_set_header X-Real-IP $remote_addr;
proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
proxy_set_header X-Forwarded-Proto $scheme;
```

Keep the ACME challenge location on port 80 and redirect every other HTTP request to HTTPS. Configure TLS using each domain's own Certbot files.

- [ ] **Step 6: Validate reload and renewal behavior**

Run the aaPanel Nginx configuration test before reloading. Enable the system Certbot renewal timer, add an executable deploy hook that tests aaPanel Nginx and reloads it, then run `certbot renew --dry-run`.

Expected: Nginx validation and renewal dry run succeed. A temporary `502` before application startup is acceptable; TLS errors are not.

### Task 6: Freeze source writes and restore the authoritative final database

**Files:**
- Create: source `/root/migration-backups/${FINAL_MIGRATION_STAMP}/shorturl-final.dump`
- Create: source and target matching SHA-256 files and count snapshots.

**Interfaces:**
- Consumes: a successful rehearsal, healthy empty target database, and source at revision `a9e56b03bf5f`.
- Produces: an authoritative target database at `20260912_refactor` with all preserved records and the approved Facebook policy.

- [ ] **Step 1: Record final source state and stop only the source FastAPI app**

In the source backend directory `/root/dockers/short-url-backend`, record final counts and revision, then run `docker compose stop app`. Confirm the app container is stopped while PostgreSQL remains healthy. Immediately record counts again and require them to match the pre-stop snapshot.

Expected: no backend process can create new access logs; source PostgreSQL remains available for dumping.

- [ ] **Step 2: Create and checksum the authoritative final dump**

Create a new exact mode-700 source backup directory, run `pg_dump -Fc --no-owner --no-acl`, validate it with `pg_restore --list`, and write its SHA-256 digest.

Expected: all commands succeed; on any failure, restart source app before further investigation.

- [ ] **Step 3: Transfer and verify the final dump twice**

Copy the dump and checksum source → operator backup directory → `/root/docker/migration-backups/` on target. Run `sha256sum -c` after each transfer.

Expected: both verification passes report `OK`; the same digest is recorded at all three locations.

- [ ] **Step 4: Recreate only the known-empty target application database**

First query target counts and require every application table is absent or empty. Confirm the Compose project directory resolves exactly to `/root/docker/short-url-backend` and the database container belongs to that project. In the same shell, terminate connections to target database `shorturl`, drop only that database, recreate it owned by `shorturl`, and restore with `pg_restore --exit-on-error --no-owner --no-acl`.

Expected: the restored target revision is `a9e56b03bf5f` and its counts equal the final frozen source snapshot.

- [ ] **Step 5: Apply the exact guarded pre-migration Facebook update**

Run the SQL from Task 3 Step 5 against the target copy and archive the original row in the target backup directory before changing it.

Expected: exactly one row changes. Otherwise stop, leave source stopped only long enough to diagnose promptly, and do not run Alembic.

- [ ] **Step 6: Run the application migration to head**

From `/root/docker/short-url-backend`, run:

```bash
docker compose run --rm app alembic upgrade head
docker compose run --rm app alembic current
```

Expected: both commands succeed and current revision is `20260912_refactor`.

- [ ] **Step 7: Apply the exact guarded post-migration Facebook policy**

Run the SQL from Task 3 Step 6 against the target database.

Expected: exactly one policy changes and the final six policy fields exactly match Global Constraints.

- [ ] **Step 8: Backfill parsed user-agent snapshots**

Run:

```bash
docker compose run --rm app python scripts/backfill_user_agents.py --batch-size 500
```

Then query `access_logs` and require zero rows where `ua_raw IS NOT NULL AND ua_browser IS NULL`.

- [ ] **Step 9: Verify final database invariants before serving traffic**

Compare source and target counts for users, domains, short links, target URLs, access logs, and blacklist rows. Compare all nine short-link IDs/codes and every target URL's ID, type, weight, active flag, and URL hash. Verify foreign-key orphan counts are zero and the active domain is `juezhou.top`.

Expected: all comparisons are exact. On failure, do not start target app; rebuild target DB from the authoritative dump before retrying.

### Task 7: Start the target application and perform end-to-end acceptance

**Files:**
- No committed files; runtime state and evidence only.

**Interfaces:**
- Consumes: the accepted migrated database, production environment, built images, and valid Nginx/TLS configuration.
- Produces: a working production service on `juezhou.top` and `shotcc.shop` with recorded smoke-test evidence.

- [ ] **Step 1: Start backend, GeoIP updater, and frontend**

From the backend directory, run `docker compose up -d app`, wait for startup, then run the `geoipupdate` profile once. From the frontend directory, run `docker compose up -d --build`.

Expected: backend, PostgreSQL, Redis, and frontend are running; GeoIP database is present. A GeoIP update provider failure is recorded but does not destroy the existing database file.

- [ ] **Step 2: Validate local origin endpoints before public smoke tests**

On the target, request `http://127.0.0.1:18000/health` with `Host: juezhou.top` and request `http://127.0.0.1:18080/` with `Host: shotcc.shop`.

Expected: backend returns JSON `{"status":"ok"}` and frontend returns its HTML shell.

- [ ] **Step 3: Reload the already-tested final Nginx configuration**

Run `/www/server/nginx/sbin/nginx -t` and reload only on success.

Expected: unrelated virtual hosts remain present and Nginx stays running.

- [ ] **Step 4: Run public HTTP/TLS and API smoke tests**

Verify:

```text
http://juezhou.top/...     -> HTTPS redirect
http://shotcc.shop/...     -> HTTPS redirect
https://juezhou.top/health -> 200 and {"status":"ok"}
https://shotcc.shop/       -> frontend HTML
https://shotcc.shop/api/auth/me -> 401 without a session, with no server error
```

Expected: certificate hostname and chain validation pass without `-k`.

- [ ] **Step 5: Verify administrator workflows through the browser**

Log in through `https://shotcc.shop` with the preserved administrator account. Open Dashboard, Short Links, Access Logs, Users, and Security. Query existing short links and logs, open link editing, and verify country options and parsed UA display.

Expected: no erroneous login/refresh toast, all API calls complete, and migrated records render correctly.

- [ ] **Step 6: Verify representative redirect behavior without mutating policies**

Use existing codes and controlled Host/Referer/User-Agent headers to verify an allowed destination and a blocked destination. For `US3tHH`, confirm that `facebook.com` and a subdomain match, an unrelated hostname containing `facebook` does not, non-DE traffic blocks, bots block, and proxy-classified traffic blocks when provider data is available.

Expected: each generated access log includes request URL, domain, code, country, parsed UA fields, Referer, result, and a specific block reason when blocked.

- [ ] **Step 7: Verify real client address handling**

Send one direct request to the target origin with a forged `CF-Connecting-IP` and one request through Cloudflare. Inspect only the corresponding access-log rows.

Expected: direct traffic cannot forge the stored client address; Cloudflare traffic stores the real client address rather than a Cloudflare edge address.

- [ ] **Step 8: Review logs and final runtime state**

Inspect recent backend, frontend, PostgreSQL, Redis, and aaPanel Nginx error logs. Run `docker compose ps`, verify no restart loops, rerun final row counts, and store a sanitized acceptance report under `/root/docker/migration-backups/`.

Expected: no new application exceptions, database errors, Nginx upstream errors, or count drift attributable to migration. New post-cutover access logs are expected and documented separately from the frozen count.

### Task 8: Close the cutover while preserving rollback assets

**Files:**
- Preserve: all source data, both final dumps, checksums, snapshots, and target Nginx backups.

**Interfaces:**
- Consumes: successful Task 7 acceptance evidence.
- Produces: a completed migration report with explicit retained rollback points.

- [ ] **Step 1: Record final deployed identities**

Record backend/frontend Git SHAs, image IDs, container statuses, certificate expiry dates, target Alembic revision, final source snapshot counts, target pre-traffic counts, and target post-smoke counts. Do not record secret values.

- [ ] **Step 2: Confirm source rollback assets remain recoverable**

Verify the source PostgreSQL container/volume, source application directory, authoritative dump, checksum, and original Nginx files still exist. Leave the source FastAPI app stopped after successful acceptance to prevent split writes.

- [ ] **Step 3: Document the immediate rollback command path**

The report must state that any post-cutover rollback starts by stopping target app/frontend, restarting the source app from `/root/dockers/short-url-backend`, and repointing the Cloudflare origin/DNS to `47.236.81.129`. Database rollback uses the preserved source database, not an Alembic downgrade.

- [ ] **Step 4: Run one final non-mutating production check**

Request both public HTTPS sites, confirm the admin API session endpoint behaves normally, inspect container status, and test Nginx configuration.

Expected: all checks succeed; migration is complete. Do not delete the old server, volumes, directories, dumps, or backups without a new explicit authorization.
