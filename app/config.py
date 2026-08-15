from typing import Literal

from pydantic import ConfigDict, model_validator
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
        if not self.database_url.strip():
            errors.append("DATABASE_URL must not be empty")
        if not self.redis_url.strip():
            errors.append("REDIS_URL must not be empty")
        if errors:
            raise ValueError("; ".join(errors))
        return self

    def public_summary(self) -> dict[str, str | bool]:
        return {
            "app_env": self.app_env,
            "cookie_secure": self.cookie_secure,
            "cors_origins": self.cors_origins,
            "log_level": self.log_level,
            "trust_proxy_headers": self.trust_proxy_headers,
        }


settings = Settings()
