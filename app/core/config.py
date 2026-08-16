from typing import Literal

from pydantic import ConfigDict, field_validator, model_validator
from pydantic_settings import BaseSettings


DEV_SECRET_VALUES = {
    "dev-secret",
    "dev-secret-key-change-in-production",
    "dev-secret-key-change-me-in-production",
    "change-me-to-a-random-secret-key-at-least-32-characters",
}


class Settings(BaseSettings):
    model_config = ConfigDict(env_file=".env", extra="ignore")

    app_env: Literal["dev", "prod"] = "dev"
    database_url: str = "postgresql+psycopg://shorturl:shorturl@localhost:5432/shorturl"
    redis_url: str = "redis://localhost:6379/0"
    secret_key: str = "dev-secret-key-change-in-production"
    geoip_db_path: str | None = None
    access_token_expire_minutes: int = 60 * 24
    cookie_secure: bool = False
    cors_origins: str = "http://localhost:3000,http://localhost:5173"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "DEBUG"
    trust_proxy_headers: bool = False
    maxmind_insights_enabled: bool = False
    maxmind_account_id: int | None = None
    maxmind_license_key: str | None = None
    maxmind_timeout_seconds: float = 1.5

    @field_validator("maxmind_account_id", mode="before")
    @classmethod
    def normalize_blank_maxmind_account_id(cls, value):
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @property
    def cors_origin_list(self) -> list[str]:
        return [value.strip() for value in self.cors_origins.split(",") if value.strip()]

    @model_validator(mode="after")
    def validate_production(self):
        if self.app_env != "prod":
            return self
        errors = []
        if len(self.secret_key) < 32 or self.secret_key in DEV_SECRET_VALUES:
            errors.append("SECRET_KEY must be at least 32 characters and not a development value")
        if not self.cookie_secure:
            errors.append("COOKIE_SECURE must be true")
        if "*" in self.cors_origin_list:
            errors.append("CORS_ORIGINS must not contain *")
        if "database_url" not in self.model_fields_set or not self.database_url.strip():
            errors.append("DATABASE_URL must be explicitly configured and not be empty")
        if "redis_url" not in self.model_fields_set or not self.redis_url.strip():
            errors.append("REDIS_URL must be explicitly configured and not be empty")
        if errors:
            raise ValueError("; ".join(errors))
        return self

    @model_validator(mode="after")
    def validate_maxmind_insights(self):
        if self.maxmind_timeout_seconds <= 0:
            raise ValueError("MAXMIND_TIMEOUT_SECONDS must be greater than zero")
        if self.maxmind_insights_enabled and (
            self.maxmind_account_id is None or not (self.maxmind_license_key or "").strip()
        ):
            raise ValueError(
                "MAXMIND_ACCOUNT_ID and MAXMIND_LICENSE_KEY are required when "
                "MAXMIND_INSIGHTS_ENABLED is true"
            )
        return self

    def public_summary(self) -> dict[str, str | bool | float]:
        return {
            "app_env": self.app_env,
            "cookie_secure": self.cookie_secure,
            "cors_origins": self.cors_origins,
            "log_level": self.log_level,
            "trust_proxy_headers": self.trust_proxy_headers,
            "maxmind_insights_enabled": self.maxmind_insights_enabled,
            "maxmind_timeout_seconds": self.maxmind_timeout_seconds,
        }


settings = Settings()
