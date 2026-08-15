# Manual Ubuntu deployment

This runbook deploys one Short URL instance behind an existing Nginx server.

## Prerequisites

- An Ubuntu host with Docker Engine and the Docker Compose plugin installed.
- Git and an existing Nginx installation.
- DNS for the public hostname pointing to this host.
- A TLS certificate and private key already provisioned for that hostname.

The Compose services publish only to loopback. Nginx is the public HTTPS entry
point and must run on the same host as the application.

## Configure the machine

Clone the selected application revision. Each machine has its own `.env` made
from the checked-in example; do not copy a development `.env` to production.

```bash
cp .env.example .env
chmod 600 .env
```

Set production values in `.env` before starting anything:

```dotenv
APP_ENV=prod
POSTGRES_USER=shorturl
POSTGRES_PASSWORD=replace-with-a-strong-unique-database-password
POSTGRES_DB=shorturl
DATABASE_URL=postgresql+psycopg://shorturl:replace-with-a-strong-unique-database-password@db:5432/shorturl
REDIS_URL=redis://redis:6379/0
SECRET_KEY=replace-with-a-unique-non-default-secret-at-least-32-characters
COOKIE_SECURE=true
CORS_ORIGINS=https://admin.example.com,https://app.example.com
TRUST_PROXY_HEADERS=true
```

Replace `.env.example`'s development PostgreSQL credentials rather than copying
them unchanged. Choose a strong, unique `POSTGRES_PASSWORD`. `DATABASE_URL` must
use the same username, password, and database named by `POSTGRES_USER`,
`POSTGRES_PASSWORD`, and `POSTGRES_DB`; URL-encode credentials when necessary.
`DATABASE_URL` and `REDIS_URL` are mandatory and must be non-empty because
Compose rejects missing or empty values.

Use only explicit HTTPS origins in `CORS_ORIGINS`; do not use `*`. Replace every
placeholder above before deployment and keep the other required ports and
settings defined by `.env.example` unless this host deliberately changes them.

## First deployment

Run these commands from the repository root. Replace the domain and credentials
with values for this installation; keep generated administrator credentials in
the operator's secret store.

```bash
make check-config
make up
make migrate
make create-domain d=go.example.com
make create-admin u=admin p='replace-with-a-strong-password'
```

`make migrate` first performs a read-only schema preflight, applies every
pending Alembic revision, and then runs a postflight that requires the repaired
indexes and `access_logs.target_url_id ON DELETE SET NULL`. The preflight allows
an empty database and historical states that the migration chain can repair,
but rejects conflicting same-named index definitions. If it reports schema
drift, investigate and correct the reported database object; do not bypass the
check or use `alembic stamp` to hide the mismatch.

Install an Nginx server block for the short-link hostname. The certificate paths
below are examples; use the paths for the certificate already issued to this
host.

```nginx
server {
    listen 443 ssl http2;
    server_name go.example.com;

    ssl_certificate /etc/letsencrypt/live/go.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/go.example.com/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:18000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $remote_addr;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

Validate and reload the existing Nginx service after saving the block:

```bash
sudo nginx -t
sudo systemctl reload nginx
```

This configuration intentionally overwrites the forwarding values at the one
trusted proxy boundary. Do not place another proxy in front of this host without
a separately reviewed client-IP design.

## Verify the release

Check both application probes through the public hostname:

```bash
curl --fail --silent https://go.example.com/health/live
curl --fail --silent https://go.example.com/health/ready
```

Make an actual request for a known short link from an external client, supplying
a recognizable user agent and referer:

```bash
curl -I https://go.example.com/example-code \
  -A 'short-url-deployment-check/1.0' \
  -e 'https://admin.example.com/deployment-check'
```

In a separate terminal, run `make logs` and confirm the matching Uvicorn access
entry and its response status. Uvicorn access output does not contain the stored
client IP, full user agent, referer, result, or domain association. Query the
current database record to verify those fields:

```bash
docker compose exec db sh -lc 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
```

Then run this query in `psql`:

```sql
SELECT
    d.name AS configured_domain,
    a.ip AS client_ip,
    a.ua_string AS user_agent,
    a.referer,
    a.result,
    a.accessed_at
FROM access_logs AS a
JOIN domains AS d ON d.id = a.domain_id
JOIN short_links AS s ON s.id = a.short_link_id
WHERE s.short_code = 'example-code'
ORDER BY a.accessed_at DESC
LIMIT 1;
```

Confirm the client IP, recognizable user agent and referer, expected result, and
that `configured_domain` identifies `go.example.com`. Raw request `Host` is not
stored in the current schema, so defer verification of the raw header until a
later schema field records it.

Use `make up` for normal deployments and updates. `make restart` validates
configuration and force-recreates the app container, so `.env` changes take
effect. Use it for an in-place restart of an existing release after a verified
configuration change:

```bash
make restart
```

## Update

Choose the commit or release tag to deploy, pull it into the checkout, and back
up PostgreSQL before changing containers. Substitute the database username and
database name from this machine's `.env` in the backup command. Store backups
in an operator-controlled directory with owner-only permissions.

```bash
git fetch --tags origin
git checkout --detach <chosen-commit-or-tag>
BACKUP_DIR=/var/backups/short-url
sudo install -d -m 700 -o "$(id -un)" -g "$(id -gn)" "$BACKUP_DIR"
umask 077
docker compose exec -T db sh -lc 'pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB"' > "$BACKUP_DIR/postgres-before-update-$(date +%F-%H%M%S).sql"
make build
make migrate
make up
```

The update-time `make migrate` uses the same read-only preflight, Alembic
upgrade, and enforcing postflight as the first deployment. Treat any preflight
drift error as a deployment stop: investigate it rather than bypassing the
check or stamping the database revision.

`make up` recreates the services from the newly built image. Repeat the health
and short-link verification after every update. Backups can contain sensitive
data: retain them on encrypted operator-controlled storage according to the
approved retention policy, and remove an individual expired backup only after
confirming its exact path.

## Rollback

Restore the previous application commit or image and rebuild that prior release
before starting services, so Compose cannot reuse the newer image:

```bash
git fetch --tags origin
git checkout --detach <previous-commit-or-tag>
make build
make up
```

Repeat the verification steps. Do not downgrade the database unless the target
release documents a migration procedure that explicitly supports that downgrade.
