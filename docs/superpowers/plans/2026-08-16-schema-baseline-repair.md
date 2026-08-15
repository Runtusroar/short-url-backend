# Schema Baseline Repair Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Repair the five indexes accidentally removed by historical migration `32736aed11b0`, converge deployed `target_url_id` foreign-key drift to `ON DELETE SET NULL`, align SQLAlchemy metadata with the repaired database head, and prove safe empty/existing-database Alembic upgrade paths before field redesign begins.

**Architecture:** Keep every applied historical migration immutable. The linear repair chain appends idempotent index repair `c4b7e2a19f03` after `a9e56b03bf5f`, then appends foreign-key convergence repair `d6e8f0a21b35` after `c4b7e2a19f03`. Declare the repaired indexes and `SET NULL` foreign key in ORM metadata, exercise migrations in UUID-named disposable PostgreSQL databases, and add a read-only schema preflight around the Makefile migration workflow.

**Tech Stack:** Python 3.11, SQLAlchemy 2.0, Alembic 1.13, PostgreSQL 15, psycopg 3, pytest 8, Docker Compose, Make

## Global Constraints

- Do not edit, rename, squash, or delete any migration at or before `c4b7e2a19f03`.
- Keep a single linear Alembic head `d6e8f0a21b35`; `c4b7e2a19f03` is a direct child of `a9e56b03bf5f`, and `d6e8f0a21b35` is a direct child of `c4b7e2a19f03`.
- Restore exactly the five historical indexes removed by `32736aed11b0`; do not add final Phase 4 indexes early.
- The repaired index names and ordered columns are `idx_short_links_domain(domain_id)`, `idx_access_logs_domain(domain_id)`, `idx_access_logs_short_link(short_link_id)`, `idx_access_logs_plus8(short_link_id, accessed_at_plus8)`, and `idx_access_logs_dedup(short_link_id, ip, dedup_bucket)`.
- Existing tables, columns, defaults, CHECK constraints, unique constraints, unrelated foreign keys, and application behavior remain unchanged.
- `access_logs.target_url_id -> target_urls.id ON DELETE SET NULL` is the canonical contract. Revision `d6e8f0a21b35` converges correctly configured, differently named, and drifted deployed constraints to the canonical named foreign key without raw SQL.
- The `d6e8f0a21b35` downgrade preserves `SET NULL` but restores the historical name `access_logs_target_url_id_fkey`, allowing the immutable `a9e56b03bf5f` downgrade to remove it and restore its parent revision's `NO ACTION` state.
- `access_logs.short_link_id/domain_id` delete semantics, `description -> name`, soft deletion, INET fields, rule semantics, audit fields, and final composite indexes remain Phase 4 work.
- Migration tests may create and drop only databases whose names are generated in-process with prefix `shorturl_migration_` followed by exactly 32 lowercase hex characters.
- Test cleanup validates the generated database name before every `DROP DATABASE ... WITH (FORCE)` and never drops the shared `shorturl_test`, development, or production database.
- Schema preflight is read-only; it may report or reject drift but never mutates tables, indexes, constraints, or Alembic state.
- Every task uses red-green-refactor, runs relevant regression tests, and ends with an isolated commit.

---

## File Map

### Created

- `alembic/versions/c4b7e2a19f03_restore_baseline_indexes.py`
- `alembic/versions/d6e8f0a21b35_repair_target_url_fk_drift.py`
- `tests/migrations/__init__.py`
- `tests/migrations/conftest.py`
- `tests/migrations/support.py`
- `tests/migrations/test_schema_baseline.py`
- `tests/migrations/test_alembic_paths.py`
- `scripts/check_schema.py`
- `tests/deployment/test_schema_check.py`

### Modified

- `app/models/short_link.py`
- `app/models/access_log.py`
- `Makefile`
- `docs/deployment.md`
- `tests/deployment/test_deployment_config.py`

---

### Task 1: Add ORM Index Metadata and the Repair Revision

**Files:**
- Modify: `app/models/short_link.py`
- Modify: `app/models/access_log.py`
- Create: `alembic/versions/c4b7e2a19f03_restore_baseline_indexes.py`
- Create: `tests/migrations/test_schema_baseline.py`

**Interfaces:**
- Produces index repair revision `c4b7e2a19f03` with `down_revision = "a9e56b03bf5f"`; Task 4 later extends it to the final head `d6e8f0a21b35`.
- Produces the exact five indexes listed in Global Constraints.
- Preserves `AccessLog.target_url_id` foreign key `ondelete="SET NULL"`.

- [ ] **Step 1: Write failing metadata and migration contracts**

Create `tests/migrations/test_schema_baseline.py`:

```python
from importlib import import_module

from app.models import AccessLog, ShortLink


EXPECTED = {
    "short_links": {
        "idx_short_links_domain": ("domain_id",),
    },
    "access_logs": {
        "idx_access_logs_domain": ("domain_id",),
        "idx_access_logs_short_link": ("short_link_id",),
        "idx_access_logs_plus8": ("short_link_id", "accessed_at_plus8"),
        "idx_access_logs_dedup": ("short_link_id", "ip", "dedup_bucket"),
    },
}


def index_map(table):
    return {
        index.name: tuple(column.name for column in index.columns)
        for index in table.indexes
    }


def test_orm_declares_repaired_baseline_indexes():
    assert index_map(ShortLink.__table__) == EXPECTED["short_links"]
    assert index_map(AccessLog.__table__) == EXPECTED["access_logs"]


def test_target_url_foreign_key_remains_set_null():
    foreign_keys = list(AccessLog.__table__.c.target_url_id.foreign_keys)
    assert len(foreign_keys) == 1
    assert foreign_keys[0].ondelete == "SET NULL"


def test_index_repair_revision_extends_a9_repair():
    revision = import_module(
        "alembic.versions.c4b7e2a19f03_restore_baseline_indexes"
    )
    assert revision.revision == "c4b7e2a19f03"
    assert revision.down_revision == "a9e56b03bf5f"
```

- [ ] **Step 2: Run red**

Run:

```bash
uv run pytest -q tests/migrations/test_schema_baseline.py
```

Expected: missing ORM indexes and migration module fail.

- [ ] **Step 3: Declare identical ORM indexes**

Import `Index` and extend existing table arguments without changing the existing unique constraint:

```python
# app/models/short_link.py
__table_args__ = (
    UniqueConstraint("domain_id", "short_code", name="uq_domain_short_code"),
    Index("idx_short_links_domain", "domain_id"),
)
```

Add to `AccessLog`:

```python
__table_args__ = (
    Index("idx_access_logs_domain", "domain_id"),
    Index("idx_access_logs_short_link", "short_link_id"),
    Index("idx_access_logs_plus8", "short_link_id", "accessed_at_plus8"),
    Index("idx_access_logs_dedup", "short_link_id", "ip", "dedup_bucket"),
)
```

- [ ] **Step 4: Add the append-only repair migration**

Create `alembic/versions/c4b7e2a19f03_restore_baseline_indexes.py`:

```python
"""restore baseline indexes removed by 32736aed11b0

Revision ID: c4b7e2a19f03
Revises: a9e56b03bf5f
"""

from alembic import op


revision = "c4b7e2a19f03"
down_revision = "a9e56b03bf5f"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "idx_short_links_domain",
        "short_links",
        ["domain_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        "idx_access_logs_domain",
        "access_logs",
        ["domain_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        "idx_access_logs_short_link",
        "access_logs",
        ["short_link_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        "idx_access_logs_plus8",
        "access_logs",
        ["short_link_id", "accessed_at_plus8"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        "idx_access_logs_dedup",
        "access_logs",
        ["short_link_id", "ip", "dedup_bucket"],
        unique=False,
        if_not_exists=True,
    )


def downgrade() -> None:
    op.drop_index("idx_access_logs_dedup", table_name="access_logs", if_exists=True)
    op.drop_index("idx_access_logs_plus8", table_name="access_logs", if_exists=True)
    op.drop_index("idx_access_logs_short_link", table_name="access_logs", if_exists=True)
    op.drop_index("idx_access_logs_domain", table_name="access_logs", if_exists=True)
    op.drop_index("idx_short_links_domain", table_name="short_links", if_exists=True)
```

- [ ] **Step 5: Verify metadata, migration chain, and regression suite**

Run:

```bash
uv run pytest -q tests/migrations/test_schema_baseline.py tests/architecture/test_model_layout.py
uv run alembic heads
uv run alembic history
uv run pytest -q
git diff --check
```

Expected: metadata tests pass, history remains linear, and the application suite passes. After Task 4, the sole head is `d6e8f0a21b35`.

- [ ] **Step 6: Commit**

```bash
git add app/models alembic/versions/c4b7e2a19f03_restore_baseline_indexes.py tests/migrations
git commit -m "fix: restore baseline database indexes"
```

---

### Task 2: Prove Empty, Existing, and Round-Trip Alembic Paths

**Files:**
- Create: `tests/migrations/__init__.py`
- Create: `tests/migrations/conftest.py`
- Create: `tests/migrations/support.py`
- Create: `tests/migrations/test_alembic_paths.py`

**Interfaces:**
- Produces fixture `migration_database_url() -> str`, backed by a unique disposable PostgreSQL database.
- Produces helpers `run_alembic(database_url, *args)`, `get_indexes(database_url, table_name)`, and `get_foreign_keys(database_url, table_name)`.
- Never mutates the shared `shorturl_test` schema except for the existing root fixture's normal setup/teardown.

- [ ] **Step 1: Write the migration-path tests against a missing support module and verify red**

Create `tests/migrations/test_alembic_paths.py` with the four tests shown in Step 4, importing `get_foreign_keys`, `get_indexes`, `run_alembic`, and `target_url_ondelete` from `tests.migrations.support`.

Run:

```bash
uv run pytest -q tests/migrations/test_alembic_paths.py
```

Expected: collection fails with `ModuleNotFoundError: tests.migrations.support`.

- [ ] **Step 2: Add a safe disposable-database fixture**

In `tests/migrations/conftest.py`, use `sqlalchemy.engine.make_url` and a synchronous psycopg admin connection. Generate the name as:

```python
DATABASE_PREFIX = "shorturl_migration_"
DATABASE_NAME_RE = re.compile(r"^shorturl_migration_[0-9a-f]{32}$")
database_name = f"{DATABASE_PREFIX}{uuid.uuid4().hex}"
assert DATABASE_NAME_RE.fullmatch(database_name)
```

Build the admin URL from the configured test URL with database `postgres`, connect with `isolation_level="AUTOCOMMIT"`, and execute a quoted `CREATE DATABASE`. Before cleanup, validate the name again, terminate connections only for that exact name, and execute:

```sql
DROP DATABASE "<validated generated name>" WITH (FORCE)
```

Render URLs with `hide_password=False`; never print them.

- [ ] **Step 3: Add Alembic and inspection helpers**

Create `tests/migrations/support.py`. `run_alembic` invokes the current interpreter and repository config:

```python
def run_alembic(database_url: str, *arguments: str) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["DATABASE_URL"] = database_url
    return subprocess.run(
        [sys.executable, "-m", "alembic", *arguments],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=True,
    )
```

Use `sqlalchemy.inspect(create_engine(database_url))` for index and foreign-key maps and always dispose engines.

- [ ] **Step 4: Complete the migration path assertions**

Use this exact test body in `tests/migrations/test_alembic_paths.py`:

```python
EXPECTED_INDEXES = {
    "short_links": {"idx_short_links_domain": ("domain_id",)},
    "access_logs": {
        "idx_access_logs_domain": ("domain_id",),
        "idx_access_logs_short_link": ("short_link_id",),
        "idx_access_logs_plus8": ("short_link_id", "accessed_at_plus8"),
        "idx_access_logs_dedup": ("short_link_id", "ip", "dedup_bucket"),
    },
}


def test_empty_database_upgrades_to_repaired_head(migration_database_url):
    run_alembic(migration_database_url, "upgrade", "head")
    assert get_indexes(migration_database_url, "short_links") == EXPECTED_INDEXES["short_links"]
    assert get_indexes(migration_database_url, "access_logs") == EXPECTED_INDEXES["access_logs"]
    assert target_url_ondelete(migration_database_url) == "SET NULL"


def test_existing_old_head_upgrades_to_repaired_head(migration_database_url):
    run_alembic(migration_database_url, "upgrade", "a9e56b03bf5f")
    assert get_indexes(migration_database_url, "short_links") == {}
    assert get_indexes(migration_database_url, "access_logs") == {}
    run_alembic(migration_database_url, "upgrade", "head")
    assert get_indexes(migration_database_url, "short_links") == EXPECTED_INDEXES["short_links"]
    assert get_indexes(migration_database_url, "access_logs") == EXPECTED_INDEXES["access_logs"]


def test_repair_revision_round_trip(migration_database_url):
    run_alembic(migration_database_url, "upgrade", "head")
    run_alembic(migration_database_url, "downgrade", "a9e56b03bf5f")
    assert get_indexes(migration_database_url, "short_links") == {}
    assert get_indexes(migration_database_url, "access_logs") == {}
    run_alembic(migration_database_url, "upgrade", "head")
    assert get_indexes(migration_database_url, "access_logs") == EXPECTED_INDEXES["access_logs"]


def test_alembic_check_has_no_pending_operations(migration_database_url):
    run_alembic(migration_database_url, "upgrade", "head")
    result = run_alembic(migration_database_url, "check")
    assert "No new upgrade operations detected" in result.stdout
```

Inspection helpers must filter out PostgreSQL implicit unique/PK indexes and return only the named baseline indexes relevant to the table.

- [ ] **Step 5: Prove the tests detect the historical broken head**

Before the repair revision is applied in the old-head test, assert the five expected indexes are absent at `a9e56b03bf5f`. This is the red evidence for the historical defect; do not temporarily modify committed migrations.

- [ ] **Step 6: Run migration tests without parallel pytest**

Run:

```bash
uv run pytest -q tests/migrations/test_alembic_paths.py
uv run pytest -q tests/migrations tests/architecture/test_model_layout.py
uv run pytest -q
git diff --check
```

Expected: four migration-path tests pass, the full suite passes, and every disposable database is removed even when a test fails.

- [ ] **Step 7: Commit**

```bash
git add tests/migrations
git commit -m "test: verify alembic upgrade paths"
```

---

### Task 3: Add Read-Only Schema Preflight and Migration Workflow

**Files:**
- Create: `scripts/check_schema.py`
- Create: `tests/deployment/test_schema_check.py`
- Modify: `Makefile`
- Modify: `tests/deployment/test_deployment_config.py`
- Modify: `docs/deployment.md`

**Interfaces:**
- Produces CLI `python scripts/check_schema.py --mode pre|post`.
- `pre` allows an empty schema or missing repair indexes, rejects same-named indexes with wrong ordered columns/uniqueness, and reports target FK state.
- `post` requires the five exact indexes and `target_url_id ON DELETE SET NULL`.
- Output is one JSON object with no database URL, password, username, hostname, SQL text, or exception traceback.
- Produces Make target `check-schema`; `migrate` runs preflight, Alembic upgrade, then postflight.

- [ ] **Step 1: Write failing pure schema-check tests**

Factor the CLI around pure function:

```python
def evaluate_schema(
    *,
    mode: str,
    tables: set[str],
    indexes: dict[str, dict[str, tuple[str, ...]]],
    target_url_ondelete: str | None,
) -> dict[str, object]:
    ...
```

Tests cover:

```python
def test_pre_allows_empty_database(): ...
def test_pre_allows_all_five_indexes_missing(): ...
def test_pre_rejects_same_named_wrong_index_columns(): ...
def test_post_requires_every_expected_index(): ...
def test_post_requires_target_url_set_null(): ...
def test_public_output_contains_no_connection_values(): ...
```

Expected JSON shape:

```json
{
  "mode": "post",
  "status": "ok",
  "schema": "ready",
  "indexes": "valid",
  "target_url_ondelete": "SET NULL"
}
```

Errors use `status="error"`, a stable `error_code`, and sanitized detail naming only table/index/constraint identifiers.

- [ ] **Step 2: Run red**

Run `uv run pytest -q tests/deployment/test_schema_check.py`.

Expected: import fails because the script does not exist.

- [ ] **Step 3: Implement read-only inspection and sanitized CLI**

Use `create_engine(settings.database_url)` and `sqlalchemy.inspect`. When no application tables exist, return `schema="empty"`. Inspect only the two relevant tables and the `target_url_id` foreign key. Normalize PostgreSQL `ondelete` to uppercase; never echo the connection URL or raw exception. Exit 0 for accepted `pre` states and valid `post`; exit 1 for drift, connection failure, or incomplete `post`.

- [ ] **Step 4: Add Make workflow tests before modifying Makefile**

Extend `tests/deployment/test_deployment_config.py`:

```python
def test_migrate_runs_preflight_upgrade_and_postflight_in_order():
    makefile = (ROOT / "Makefile").read_text()
    migrate = makefile.split("migrate:", 1)[1].split("\n\n", 1)[0]
    pre = "python scripts/check_schema.py --mode pre"
    upgrade = "alembic upgrade head"
    post = "python scripts/check_schema.py --mode post"
    assert pre in migrate
    assert upgrade in migrate
    assert post in migrate
    assert migrate.index(pre) < migrate.index(upgrade) < migrate.index(post)
```

- [ ] **Step 5: Wire Makefile and deployment documentation**

Add `check-schema` to `.PHONY` and:

```makefile
check-schema:
	$(DOCKER_COMPOSE) run --rm app python scripts/check_schema.py --mode pre

migrate: check-schema
	$(DOCKER_COMPOSE) run --rm app alembic upgrade head
	$(DOCKER_COMPOSE) run --rm app python scripts/check_schema.py --mode post
```

Update `docs/deployment.md` so first deployment/update runs `make migrate`, explains that it performs read-only preflight and postflight, and states a preflight drift error must be investigated rather than bypassed or stamped away.

- [ ] **Step 6: Verify CLI against disposable migrated databases**

Use the Task 2 fixture to add integration coverage that runs `--mode pre` on empty/old-head databases and `--mode post` after `upgrade head`. Verify JSON is parseable/sanitized and exit codes match.

Run:

```bash
uv run pytest -q tests/deployment/test_schema_check.py tests/deployment/test_deployment_config.py tests/migrations
make -n migrate
uv run pytest -q
.venv/bin/python -m compileall -q app tests scripts alembic
uv run alembic heads
docker compose --env-file .env.example config --quiet
git diff --check
```

- [ ] **Step 7: Commit**

```bash
git add scripts/check_schema.py tests/deployment Makefile docs/deployment.md
git commit -m "build: validate schema migration state"
```

---

### Task 4: Post-Review Drift Repair

**Files:**
- Create: `alembic/versions/d6e8f0a21b35_repair_target_url_fk_drift.py`
- Modify: `tests/migrations/support.py`
- Modify: `tests/migrations/test_alembic_paths.py`
- Modify: `tests/migrations/test_schema_baseline.py`
- Modify: `docs/superpowers/plans/2026-08-16-schema-baseline-repair.md`

**Interfaces:**
- Produces the sole Alembic head `d6e8f0a21b35` with `down_revision = "c4b7e2a19f03"`.
- Repairs databases whose Alembic version was stamped past `a9e56b03bf5f` while `access_logs.target_url_id` retained `NO ACTION`.
- Uses `op.get_bind()` and SQLAlchemy inspection to drop every foreign key whose ordered constrained columns are exactly `target_url_id`, using only inspector-returned names, then creates canonical `fk_access_logs_target_url ON DELETE SET NULL`.
- Leaves the five baseline indexes intact; downgrade safely normalizes the FK to the historical name and `SET NULL` semantics expected by the parent migration chain.

- [ ] **Step 1: Prove the stamped drift path is red**

Upgrade a disposable database to `b1e5f6851085`, verify the target URL foreign
key has no delete action, stamp it to `a9e56b03bf5f`, and upgrade to head. Before
the new revision, assert that the missing `SET NULL` contract fails while the
revision-chain contract also fails because `d6e8f0a21b35` does not exist.

- [ ] **Step 2: Add the append-only convergence revision**

Inspect `access_logs` foreign keys through SQLAlchemy, match constrained columns
exactly, safely drop all matching named constraints, and create the canonical
foreign key. Apply the same inspection in downgrade while restoring the
historical constraint name required by `a9e56b03bf5f`. Do not edit any existing
revision or use raw SQL.

- [ ] **Step 3: Verify migration paths and the whole application**

Run focused migration tests, the full serial suite, Alembic heads/history,
`alembic check` on a disposable head database, compileall, and diff checks.
Confirm the disposable stamped path ends with all five baseline indexes and
`target_url_id ON DELETE SET NULL`.

- [ ] **Step 4: Commit and record review evidence**

Commit the revision, tests, and plan/progress updates as one isolated
post-review drift repair and write `task-4-report.md`.

---

## Phase 3 Completion Gate

Phase 3 is complete only when:

- Alembic has one linear head `d6e8f0a21b35`.
- All five accidentally removed indexes exist both in ORM metadata and a database upgraded to head.
- `access_logs.target_url_id` is `ON DELETE SET NULL` in ORM, a normally migrated PostgreSQL database, and a database stamped from the historical `NO ACTION` state.
- An empty disposable database upgrades to head.
- A disposable database at `a9e56b03bf5f` upgrades to repaired head.
- A disposable database upgraded to `b1e5f6851085`, stamped to `a9e56b03bf5f`, and upgraded to head converges its target URL foreign key while retaining all five indexes.
- Repair downgrade/upgrade round-trip passes.
- Downgrading from head through `a9e56b03bf5f` to `b1e5f6851085` succeeds and restores the historical `NO ACTION` target URL foreign key.
- `alembic check` reports no pending operations against a migrated disposable database.
- Preflight accepts empty/repairable states, rejects conflicting same-named indexes, and postflight requires the repaired schema.
- `make migrate` orders preflight -> upgrade -> postflight.
- No historical migration changed and no Phase 4 field/constraint behavior was introduced.
