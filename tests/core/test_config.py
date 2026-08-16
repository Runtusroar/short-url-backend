import pytest
from pydantic import ValidationError

from app.core.config import Settings


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


def test_maxmind_insights_is_disabled_by_default_and_blank_account_id_is_none():
    settings = make_settings(maxmind_account_id="", maxmind_license_key="")

    assert settings.maxmind_insights_enabled is False
    assert settings.maxmind_account_id is None
    assert settings.maxmind_license_key.get_secret_value() == ""
    assert settings.maxmind_timeout_seconds == 1.5


def test_maxmind_insights_enabled_requires_credentials():
    with pytest.raises(ValidationError, match="MAXMIND_ACCOUNT_ID and MAXMIND_LICENSE_KEY"):
        make_settings(maxmind_insights_enabled=True, maxmind_account_id=123)


def test_maxmind_insights_enabled_accepts_nonblank_credentials():
    settings = make_settings(
        maxmind_insights_enabled=True,
        maxmind_account_id=123,
        maxmind_license_key="license-key",
        maxmind_timeout_seconds=2.5,
    )

    assert settings.maxmind_account_id == 123
    assert settings.maxmind_license_key.get_secret_value() == "license-key"
    assert settings.maxmind_timeout_seconds == 2.5


def test_maxmind_license_key_is_redacted_in_settings_repr():
    secret = "never-expose-this-license-key"
    settings = make_settings(
        maxmind_insights_enabled=True,
        maxmind_account_id=123,
        maxmind_license_key=secret,
    )

    assert secret not in repr(settings)


def assert_secret_is_absent_from_validation_output(secret, error):
    for output in (
        str(error),
        repr(error),
        repr(error.errors()),
        error.json(),
    ):
        assert secret not in output


@pytest.mark.parametrize(
    "overrides",
    [
        {
            "maxmind_insights_enabled": True,
            "maxmind_license_key": "distinctive-license-secret",
        },
        {
            "app_env": "prod",
            "secret_key": "x" * 32,
            "cookie_secure": False,
            "cors_origins": "https://admin.example.com",
            "maxmind_insights_enabled": True,
            "maxmind_account_id": 123,
            "maxmind_license_key": "distinctive-license-secret",
        },
    ],
)
def test_maxmind_license_key_is_absent_from_all_validation_outputs(overrides):
    secret = "distinctive-license-secret"

    with pytest.raises(ValidationError) as raised:
        make_settings(**overrides)

    assert_secret_is_absent_from_validation_output(secret, raised.value)


def test_maxmind_insights_enabled_rejects_missing_license_key():
    with pytest.raises(ValidationError, match="MAXMIND_ACCOUNT_ID and MAXMIND_LICENSE_KEY"):
        make_settings(maxmind_insights_enabled=True, maxmind_account_id=123)


def test_env_maxmind_license_key_is_absent_from_validation_outputs(monkeypatch):
    secret = "distinctive-environment-license-secret"
    monkeypatch.setenv("MAXMIND_LICENSE_KEY", secret)

    with pytest.raises(ValidationError) as raised:
        Settings(_env_file=None, maxmind_insights_enabled=True)

    assert_secret_is_absent_from_validation_output(secret, raised.value)


@pytest.mark.parametrize(
    "timeout",
    [float("nan"), float("inf"), float("-inf"), 0, -1],
    ids=("nan", "positive-infinity", "negative-infinity", "zero", "negative"),
)
def test_maxmind_insights_requires_finite_positive_timeout(timeout):
    with pytest.raises(
        ValidationError,
        match="MAXMIND_TIMEOUT_SECONDS must be finite and greater than zero",
    ):
        make_settings(maxmind_timeout_seconds=timeout)


def test_maxmind_insights_accepts_finite_positive_timeout():
    settings = make_settings(maxmind_timeout_seconds=0.125)

    assert settings.maxmind_timeout_seconds == 0.125


def test_invalid_maxmind_timeout_keeps_license_secret_out_of_validation_outputs():
    secret = "distinctive-timeout-license-secret"

    with pytest.raises(ValidationError) as raised:
        make_settings(
            maxmind_insights_enabled=True,
            maxmind_account_id=123,
            maxmind_license_key=secret,
            maxmind_timeout_seconds=float("inf"),
        )

    assert_secret_is_absent_from_validation_output(secret, raised.value)


def test_public_summary_redacts_maxmind_credentials():
    settings = make_settings(
        maxmind_insights_enabled=True,
        maxmind_account_id=123,
        maxmind_license_key="do-not-log-this",
    )

    assert settings.public_summary()["maxmind_insights_enabled"] is True
    assert settings.public_summary()["maxmind_timeout_seconds"] == 1.5
    assert "maxmind_account_id" not in settings.public_summary()
    assert "maxmind_license_key" not in settings.public_summary()
    assert "do-not-log-this" not in settings.public_summary().values()


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


@pytest.mark.parametrize(
    ("field_name", "environment_name", "message"),
    [
        ("database_url", "DATABASE_URL", "DATABASE_URL"),
        ("redis_url", "REDIS_URL", "REDIS_URL"),
    ],
)
def test_prod_rejects_omitted_service_url(field_name, environment_name, message, monkeypatch):
    monkeypatch.delenv(environment_name, raising=False)
    values = {
        "app_env": "prod",
        "secret_key": "x" * 32,
        "cookie_secure": True,
        "cors_origins": "https://admin.example.com",
        "database_url": "postgresql+psycopg://user:pass@db:5432/app",
        "redis_url": "redis://redis:6379/0",
    }
    del values[field_name]
    with pytest.raises(ValidationError, match=message):
        Settings(_env_file=None, **values)


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
        "maxmind_insights_enabled": False,
        "maxmind_timeout_seconds": 1.5,
    }
