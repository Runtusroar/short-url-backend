# Short URL Production Migration Design

**Date:** 2026-09-12

## Goal

Replace the obsolete GitHub `main` branches with the verified local backend and frontend, then move the live short-link service and its PostgreSQL data from `47.236.81.129` to `47.236.145.202` without losing users, domains, links, destinations, access history, or policy intent.

The old server and final database dump remain available as rollback assets until the owner explicitly authorizes cleanup.

## Confirmed scope

- Backend repository: `Runtusroar/short-url-backend`
- Frontend repository: `Runtusroar/short-url-frontend`
- Source server: `47.236.81.129`
- Target server: `47.236.145.202`
- Short-link domain: `juezhou.top`
- Administration domain: `shotcc.shop`
- Target deployment directories:
  - `/root/docker/short-url-backend`
  - `/root/docker/short-url-frontend`
- Runtime: Docker Compose behind the target server's existing aaPanel Nginx

No unrelated aaPanel sites, Nginx virtual hosts, containers, databases, or firewall rules are to be modified.

## Observed state

### Local and GitHub

| Repository | Verified local `main` | Current remote `main` before overwrite |
| --- | --- | --- |
| Backend | `489cdd0954d332951539ca5cff5adcf5ef15f44d` | `9c4bcf58da8e9a8802a6fee381ccc56e3b828fba` |
| Frontend | `0bd7586c20d1983b329ddb6cff9811680e907a71` | `4fa6b70789767512b8476d2a36655908bb18645a` |

The backend histories have intentionally diverged. A normal pull or merge would reintroduce the obsolete implementation, so the remote branch must be replaced rather than merged.

### Source server

- Ubuntu 24.04 with Docker and Docker Compose.
- Projects are currently under `/root/dockers/short-url-backend` and `/root/dockers/short-url-frontend`.
- Backend container is exposed only at `127.0.0.1:18000`; frontend uses port `18080`.
- PostgreSQL database size is approximately 16 MB.
- Alembic revision is exactly `a9e56b03bf5f`, the parent expected by the new data-preserving migration.
- Snapshot counts at discovery time:
  - users: 1
  - domains: 1
  - short links: 9
  - target URLs: 19
  - access rules: 9
  - access logs: 28,005
  - IP blacklist entries: 0
- The active short-link domain is `juezhou.top`.

These counts are discovery values only. The final pre-cutover counts become the authoritative comparison because access logs can continue to grow before the write freeze.

### Target server

- Ubuntu 24.04 with approximately 30 GB free disk space.
- Docker is not installed.
- `/root/docker` does not yet exist.
- aaPanel Nginx is already listening on ports 80 and 443.
- Existing `indcc.top` and `zhenmei.shop` virtual hosts must remain untouched.
- UFW is active and does not expose application or database ports.

## Repository replacement

For each repository:

1. Fetch the current remote reference immediately before pushing.
2. Verify the remote `main` still equals the recorded SHA above.
3. Run the complete local verification suite.
4. Push local `main` with an explicit lease tied to the observed remote SHA.
5. Fetch again and verify remote `main` equals local `main`.

The operation uses `--force-with-lease=refs/heads/main:<expected-sha>`, not an unconditional force push. If the remote SHA has changed, the operation stops for review instead of overwriting new work.

The recorded old SHAs are the recovery references for the overwritten GitHub state.

## Target runtime architecture

The target server keeps aaPanel Nginx as the only public listener:

```text
Internet / Cloudflare
        |
aaPanel Nginx :80/:443
        |-- juezhou.top/*      -> 127.0.0.1:18000 (FastAPI redirect service)
        |-- shotcc.shop/api/*  -> 127.0.0.1:18000 (FastAPI administration API)
        `-- shotcc.shop/*      -> 127.0.0.1:18080 (frontend container)

Docker network short_url_network
        |-- app
        |-- PostgreSQL
        |-- Redis
        `-- frontend
```

PostgreSQL, Redis, FastAPI, and frontend ports bind only to loopback when a host binding is needed. They are never exposed through UFW or a public Docker bind.

Before publishing the repositories, the Compose port declarations are updated so PostgreSQL (`18543`), Redis (`16379`), FastAPI (`18000`), and the frontend (`18080`) all bind explicitly to `127.0.0.1`. This keeps the same convenient local development ports while making the checked-in deployment defaults safe on an Internet-facing host.

The backend production environment uses:

- `APP_ENV=production`
- the migrated PostgreSQL database
- the preserved application secret so the migration does not unexpectedly invalidate signed state
- `PUBLIC_SHORT_URL_SCHEME=https`
- no public short-link port
- `COOKIE_SECURE=true`
- `TRUST_PROXY_HEADERS=true`
- the existing GeoIP and MaxMind credentials without printing them in logs

Database credentials are generated for the target deployment and stored only in a root-readable `.env` file. Secret values must not be committed to Git or copied into the migration report.

## Nginx and real client addresses

The two new aaPanel virtual hosts are written as separate files in aaPanel's loaded Nginx configuration directory. Before every reload:

1. Back up any same-named target file if it exists.
2. Run the aaPanel Nginx binary's configuration test.
3. Reload only after the test succeeds.

Proxy headers are normalized at the public boundary:

```nginx
proxy_set_header Host $host;
proxy_set_header X-Real-IP $remote_addr;
proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
proxy_set_header X-Forwarded-Proto $scheme;
```

For Cloudflare traffic, only Cloudflare's published address ranges are trusted with `set_real_ip_from`; `CF-Connecting-IP` is accepted only from those ranges. Direct clients cannot forge the real client address by supplying their own forwarded headers.

`shotcc.shop/api/` proxies directly from host Nginx to FastAPI instead of passing through the frontend container's Nginx. This prevents a Docker bridge address from replacing the real visitor address.

## TLS certificates

The old `juezhou.top` certificate expired on 2026-09-01 and must not be copied. The target obtains new Let's Encrypt certificates for both domains using an HTTP webroot challenge:

1. Install Certbot from the Ubuntu repository.
2. Create HTTP-only virtual hosts that serve `/.well-known/acme-challenge/`.
3. Request certificates for `juezhou.top` and `shotcc.shop`.
4. Enable the final HTTPS proxy virtual hosts and HTTP-to-HTTPS redirects.
5. Confirm Certbot's renewal timer and add a successful-renewal Nginx reload hook.

If issuance fails, the deployment stops before enabling the HTTPS sites. It must not fall back to an expired certificate or silently weaken TLS validation.

## Data compatibility strategy

The source database remains unchanged. Every compatibility adjustment happens only in a restored target copy.

The new Alembic migration already preserves and transforms users, domains, links, target URLs, permissions, access logs, IP blacklist entries, and most simple legacy policies. One observed rule requires an explicit reviewed mapping:

| Link | Legacy rule | New policy |
| --- | --- | --- |
| `US3tHH` (`G1`) | country `DE`, full-Referer glob `*facebook*`, proxy denied, bot denied | country allow `DE`; Referer allow `facebook.com` and `*.facebook.com`; block proxy; block bot |

This deliberately replaces unsafe substring matching with the approved hostname semantics. It no longer matches unrelated domains that merely contain the word `facebook`.

The compatibility procedure is deterministic:

1. Export the affected legacy row to a root-readable migration artifact.
2. On the restored database only, clear that one legacy Referer glob with an update guarded by link ID and expected old value; abort unless exactly one row changes.
3. Run the standard Alembic upgrade to `20260912_refactor`. The remaining country, proxy, and bot behavior is converted by the tested migration.
4. Set the migrated link policy's Referer mode and the two approved hostname patterns, again guarded by the exact link ID.
5. Verify the resulting complete policy values before allowing traffic.

If any other policy fails migration preflight, the target database is discarded and restored again. No unreviewed approximation is permitted.

## Migration sequence

### Phase 1: verification and rehearsal

1. Verify both local repositories and record final commit SHAs.
2. Replace both GitHub `main` branches with lease protection.
3. Create a timestamped custom-format PostgreSQL dump on the source server and record its SHA-256 digest.
4. Restore that dump into an isolated disposable PostgreSQL instance.
5. Apply the guarded Facebook compatibility mapping.
6. Run Alembic to head and the user-agent backfill.
7. Run schema, count, relationship, policy, and representative redirect checks.

The source service remains available throughout the rehearsal.

### Phase 2: target preparation

1. Install Docker Engine and the Compose plugin from Ubuntu packages.
2. Create `/root/docker` and a root-only backup directory.
3. Clone the newly replaced GitHub repositories.
4. Create root-readable production environment configuration.
5. Build images and start only PostgreSQL and Redis.
6. Install the HTTP challenge Nginx configuration and obtain certificates.

### Phase 3: final write freeze and restore

1. Record final source table counts and Alembic revision.
2. Stop the old FastAPI application to freeze database writes. Keep its database running.
3. Create the final custom-format dump and SHA-256 digest.
4. Transfer the dump to the target through the operator machine, verifying the digest after each transfer.
5. Restore into a fresh target PostgreSQL volume.
6. Apply the guarded compatibility mapping.
7. Run Alembic to head and verify `20260912_refactor`.
8. Apply and verify the new Referer policy.
9. Run the idempotent user-agent backfill.
10. Start FastAPI and the frontend.
11. Enable and validate the final aaPanel Nginx configuration.

The final dump is authoritative; no writes are merged from the rehearsal database.

## Acceptance checks

The deployment is accepted only if all of the following pass:

- GitHub remote `main` SHAs exactly match the verified local SHAs.
- All target containers are running and healthy where health checks exist.
- Target Alembic revision is `20260912_refactor`.
- Final source and target counts match for users, domains, short links, target URLs, access logs, and blacklist rows.
- All nine short-link IDs and codes are present.
- All target URL IDs, types, weights, and active flags are preserved.
- `US3tHH` has the explicitly approved Facebook hostname policy plus its country/proxy/bot restrictions.
- Access-log snapshot fields are populated according to the migration contract.
- The admin can log in through `https://shotcc.shop` and query short links and access logs.
- `https://juezhou.top/health` succeeds through Nginx.
- Representative `juezhou.top` short codes select the expected allowed or blocked destination.
- An Nginx request test demonstrates the original Host, scheme, and client-address chain reaching the backend.
- Browser and container logs contain no new application errors during the smoke test.
- Certbot reports both certificates and a successful renewal dry run.

Historical access records that were blocked by the old implementation retain the migration's explicit historical fallback reason because the previous schema did not store a more specific cause.

## Failure handling and rollback

Before the old application is stopped, any target failure is isolated and has no production data effect.

After the write freeze:

- If dump, transfer, restore, migration, count validation, container startup, certificate issuance, or Nginx validation fails, do not advertise the target as successful.
- Disable the incomplete target virtual hosts if necessary.
- Restart the old FastAPI application immediately.
- Retain the failed target database for diagnosis only if it contains no newer writes; otherwise replace it from the final dump before retrying.
- Because DNS already points at the target, restoring public service on the old server may also require the owner to temporarily point the origin records back to `47.236.81.129`.

The old application directory, old PostgreSQL volume, source-side final dump, transferred dump, and recorded checksums remain intact after successful cutover. Their deletion is a separate destructive action requiring explicit authorization.

## Out of scope

- Deleting or repurposing the old server.
- Removing migration backups.
- Changing unrelated aaPanel sites.
- Introducing CI/CD in this migration.
- Redesigning application behavior beyond the single approved legacy Facebook rule conversion.
