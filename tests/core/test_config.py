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
    }
