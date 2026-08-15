from pathlib import Path


DOC = Path(__file__).resolve().parents[2] / "docs" / "deployment.md"


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


def test_update_backup_uses_restricted_operator_directory():
    text = DOC.read_text()
    assert "BACKUP_DIR=/var/backups/short-url" in text
    assert 'install -d -m 700 -o "$(id -un)" -g "$(id -gn)" "$BACKUP_DIR"' in text
    assert "umask 077" in text
    assert '"$BACKUP_DIR/postgres-before-update-$(date +%F-%H%M%S).sql"' in text


def test_rollback_rebuilds_previous_commit_before_starting_services():
    text = DOC.read_text()
    rollback = text.split("## Rollback", maxsplit=1)[1]
    checkout = rollback.index("git checkout --detach <previous-commit-or-tag>")
    build = rollback.index("make build")
    up = rollback.index("make up")
    assert checkout < build < up


def test_production_requires_nondevelopment_service_credentials():
    text = DOC.read_text()
    compact = " ".join(text.split())
    assert "`DATABASE_URL` and `REDIS_URL` are mandatory and must be non-empty" in compact
    assert "Replace `.env.example`'s development PostgreSQL credentials" in compact
    assert "strong, unique `POSTGRES_PASSWORD`" in compact
    assert (
        "`DATABASE_URL` must use the same username, password, and database named by "
        "`POSTGRES_USER`, `POSTGRES_PASSWORD`, and `POSTGRES_DB`"
    ) in compact
    assert "postgresql+psycopg://shorturl:shorturl" not in text
    assert "pg_dump -U shorturl shorturl" not in text


def test_restart_recreates_app_after_config_validation():
    text = " ".join(DOC.read_text().split())
    assert (
        "`make restart` validates configuration and force-recreates the app container, "
        "so `.env` changes take effect"
    ) in text
    assert "Use `make up` for normal deployments and updates" in text


def test_verification_distinguishes_access_output_from_stored_request_data():
    text = DOC.read_text()
    compact = " ".join(text.split())
    assert "Uvicorn access entry and its response status" in compact
    for field in (
        "d.name AS configured_domain",
        "a.ip AS client_ip",
        "a.ua_string AS user_agent",
        "a.referer",
        "a.result",
    ):
        assert field in text
    assert "Raw request `Host` is not stored" in compact
    assert "application log that the entry records the expected `Host`" not in compact
