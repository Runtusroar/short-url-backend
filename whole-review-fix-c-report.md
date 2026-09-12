# Whole Review Fix C Report

## Scope completed

- `GET /api/auth/me` now explicitly queries the current user's `user_domain_access` rows. Subaccounts receive ordered `read`/`manage` grants; administrators explicitly receive `domain_access: []` because their role is globally scoped.
- `GET /api/short-links` now uses one aggregate page query (plus the existing total query) to return `short_url`, destination URL summaries, policy summary, active state, visit count, and update time. It does not materialize ORM relationships per row.
- Complete list URLs use `PUBLIC_SHORT_URL_SCHEME`: development/test defaults to `http`; production rejects anything except `https`.
- PostgreSQL named unique constraints now map centrally through the existing structured diagnostic extractor to `USERNAME_CONFLICT`, `DOMAIN_CONFLICT`, and `IP_BLACKLIST_CONFLICT`. Concurrent HTTP tests use the real PostgreSQL database.
- The test database now truncates and reseeds before and after every test. Seed password hashes are cached once per test run so isolation remains fast. The concurrent administrator test also proves its issued token remains usable, and selected tests were run in both orders.
- Missing allowed and blocked destinations retain their error access-log snapshots but now return distinct stable 404 codes: `ALLOWED_TARGET_UNAVAILABLE` and `BLOCKED_TARGET_UNAVAILABLE`.
- Fixed root routes (`api`, `admin`, `static`, `health`, `docs`, `redoc`, `openapi`) are reserved consistently for custom aliases and generated codes.
- The shared HTTP exception handler now forwards exception headers, preserving protocol contracts such as `Retry-After` and `Allow`.
- The real Alembic fresh-upgrade test now asserts the final six `access_logs` indexes and confirms obsolete legacy indexes are absent. The historical migration already removes those four legacy indexes.

## TDD evidence

New API, concurrency, redirect, configuration, alias, and migration assertions were added first. The initial focused run failed for the intended absent grants, list fields, named conflict codes, target codes, scheme setting, protocol headers, and reserved paths. The aggregate query then exposed PostgreSQL's `ARRAY[]` type inference requirement; it was corrected with an explicit `TEXT[]` cast before the green run.

## Verification

- `uv run python -m compileall -q app tests`
- Focused review regression set: `19 passed`
- Reverse-order isolation regression, both orders: `2 passed` and `2 passed`
- `uv run pytest -q tests/test_migrations.py tests/test_models.py tests/api/test_route_contract.py`: `47 passed`
- `uv run pytest -q tests/api`: `142 passed`
- `make test`: `321 passed`

`DELETE /api/domains/{domain_id}` remains the batch-B soft deactivation behavior; no progress file was changed.
