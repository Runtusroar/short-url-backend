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
SECRET_KEY=replace-with-a-unique-non-default-secret-at-least-32-characters
COOKIE_SECURE=true
CORS_ORIGINS=https://admin.example.com,https://app.example.com
TRUST_PROXY_HEADERS=true
```

Use only explicit HTTPS origins in `CORS_ORIGINS`; do not use `*`. Keep the
database and Redis URLs, ports, and other required settings defined by
`.env.example` unless this host deliberately uses different values.

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

Check both application probes through the public hostname, then follow the
application logs:

```bash
curl --fail --silent https://go.example.com/health/live
curl --fail --silent https://go.example.com/health/ready
make logs
```

Make an actual request for a known short link from an external client, supplying
a recognizable user agent and referer. Confirm in the application log that the
entry records the expected `Host`, client IP, user agent, and referer.

```bash
curl -I https://go.example.com/example-code \
  -A 'short-url-deployment-check/1.0' \
  -e 'https://admin.example.com/deployment-check'
```

For an in-place application restart after a verified configuration change, use:

```bash
make restart
```

## Update

Choose the commit or release tag to deploy, pull it into the checkout, and back
up PostgreSQL before changing containers. Substitute the database username and
database name from this machine's `.env` in the backup command.

```bash
git fetch --tags origin
git checkout --detach <chosen-commit-or-tag>
docker compose exec -T db pg_dump -U shorturl shorturl > postgres-before-update.sql
make build
make migrate
make up
```

`make up` recreates the services from the newly built image. Repeat the health
and short-link verification after every update.

## Rollback

Restore the previous application commit or image, rebuild it if needed, then run
`make up` and repeat the verification steps. Do not downgrade the database
unless the target release documents a migration procedure that explicitly
supports that downgrade.
