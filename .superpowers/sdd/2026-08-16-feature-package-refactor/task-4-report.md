# Task 4 Report: Domain and Blacklist Feature Packages

## Status

Completed and committed as `refactor: package domain and blacklist features`.

## File moves

- Moved domain HTTP routes from `app/routers/domains.py` to `app/features/domains/router.py`.
- Moved `app/domains.py` to `app/features/domains/dependencies.py`; `_get_host` is now `get_request_host`.
- Moved domain schemas from `app/schemas.py` to `app/features/domains/schemas.py`.
- Moved blacklist HTTP routes from `app/routers/ip_blacklist.py` to `app/features/blacklist/router.py`.
- Moved blacklist schemas from `app/schemas.py` to `app/features/blacklist/schemas.py`.
- Added focused service modules for domain and blacklist database workflows.
- Deleted the three legacy files after all imports were migrated.

## Service and dependency interfaces

- `app.features.domains.dependencies.get_request_host(request) -> str` reads only `Host`, removes the port, and lowercases the result.
- `get_current_domain`, `has_domain_access`, and `require_domain_access` remain FastAPI-compatible dependencies and preserve default-domain lookup, access roles, and errors.
- `app.features.domains.service` owns role-scoped listing, CRUD, uniqueness checks, default-domain clearing, commits, refreshes, and not-found/conflict behavior.
- `app.features.blacklist.service` owns list/add/remove queries, duplicate handling, commits, and refreshes.
- Routers retain only HTTP dependencies, request/response schemas, and response payload conversion.

## Red-green evidence

- RED: `uv run pytest -q tests/architecture/test_domains_blacklist_layout.py tests/test_domains.py` failed during collection with `ModuleNotFoundError: No module named 'app.features.blacklist'` after adding the feature-interface contract.
- GREEN: the same command passed: `2 passed in 0.62s`.

## Verification

- `uv run pytest -q tests/architecture/test_domains_blacklist_layout.py tests/architecture/test_http_contract.py tests/test_domains.py tests/test_blacklist.py tests/test_api.py -k 'domain or blacklist or redirect'` -> `21 passed, 23 deselected in 5.14s`.
- `uv run pytest -q && git diff --check` -> `126 passed in 14.08s`; whitespace check clean.

## Risks and deviations

- No behavioral deviations: prefixes, route names, response schemas, role checks, query filters, transactions, default-domain updates, and Host-only resolution are preserved.
- The initial unprivileged `uv` invocation could not access its external cache; the identical command was rerun with approved access.
