# Whole Review Fix B Report

## Scope

This batch addresses the backend review findings for domain/Host canonicalization,
destination and platform validation, production settings, migration safety, and
domain audit retention. `progress.md` was deliberately not changed. Batch C is
out of scope.

## Contract changes

- Tenant domains and request `Host` values now share one ASCII DNS hostname
  normalizer. Names are lowercase, one trailing dot is removed, and empty labels,
  invalid labels, URI syntax, IP literals, and invalid ports are rejected.
- Domain create/update responses return the canonical name. Redirect tenant
  lookup accepts only the same DNS-host contract.
- Destination URLs must be absolute `http` or `https` URLs with a legal host and
  port. Relative URLs, non-HTTP schemes, userinfo, control characters, and
  malformed authorities return the normal 422 validation envelope.
- Policy platforms are a deduplicated closed enum shared with DeviceDetector's
  runtime platform mapping: `desktop`, `smartphone`, `tablet`, `tv`, `console`,
  `wearable`, `bot`, and `other`.
- `APP_ENV` is restricted to `development`, `test`, or `production`. Production
  requires a non-placeholder `SECRET_KEY` of at least 32 characters and
  `COOKIE_SECURE=true`. Compose and `.env.example` pass both settings explicitly.
- The schema migration validates every legacy domain before changing migration
  structures. Invalid domains and normalization collisions identify the affected
  IDs and abort while legacy rows/tables remain intact; safe rows are lowercased
  and have one trailing dot removed.
- `DELETE /api/domains/{id}` now deactivates the domain. Its `domain_id` remains
  on access logs; administrators can query logs and dashboard aggregates for an
  inactive domain. Subaccounts receive the same 403 envelope for inactive,
  missing, and ungranted domain IDs.

## Test coverage added or updated

- Shared schema/Host normalization, malformed authority handling, and canonical
  create/update API output.
- Destination URL and platform enum validation at schema and API boundaries.
- Production settings, including the unchanged `.env.example` secret placeholder.
- Real Alembic upgrades for uppercase names, trailing dots, invalid names, and
  normalization collisions, including checks that failure leaves legacy
  structures/data intact.
- Soft deletion with retained audit visibility for administrators and non-leaking
  subaccount denial behavior.

## Verification

- `uv run pytest -q` — 294 passed.
- `uv run pytest tests/api -q` — 134 passed.
- `make test` — 294 passed.
- `uv run python -m compileall -q app alembic scripts tests` and `git diff --check` — passed.
- `docker compose build app` — passed.
- The normal Compose migration attempt was blocked before Alembic by an occupied
  host port (`16379`). The same built image then ran `alembic upgrade head`
  successfully against a fresh, isolated PostgreSQL container with no published
  ports. The temporary verification container, network, stopped Compose
  containers, and unused verification volumes were removed afterwards.

## Review round 1 follow-up

- ASCII validation now occurs before any lowercase operation. Unicode case-fold
  lookalikes such as `K.example` are rejected consistently by domain APIs,
  request Host parsing, destination authorities, and migration preflight.
- Domain names and destination URLs reject leading/trailing whitespace rather
  than trimming it. Destination validation also rejects whitespace anywhere in
  the URL authority/value and checks the raw authority for non-ASCII before
  `urlsplit().hostname` can case-fold it.
- The domain-normalization algorithm in `20260912_refactor_schema` is frozen in
  the migration itself. A test changes the application helper during a real
  legacy upgrade and confirms the migration still rejects the Unicode legacy
  name without dropping old structures.
- The migration remains intentionally forward-only, as approved by the design:
  reconstructing removed legacy policy/default structures would be destructive
  and destructive downgrade support is not a release requirement. The migration
  docstring and an explicit test record that contract.
- Repeated domain DELETE calls are idempotent. An integration test holds the
  actual PostgreSQL row lock, verifies a second API DELETE blocks on it, then
  confirms the second request completes successfully after release.

### Review round 1 verification

- `DATABASE_URL=.../shorturl_review_b_test make test` — 309 passed
- `DATABASE_URL=.../shorturl_review_b_test uv run pytest tests/api -q` — 137 passed
- `DATABASE_URL=.../shorturl_review_b_test uv run pytest tests/test_migrations.py -q` — 11 passed
- `uv run python -m compileall -q app alembic scripts tests` and `git diff --check` — passed
