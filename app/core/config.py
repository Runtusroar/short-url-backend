from pydantic import ConfigDict, field_validator
from pydantic_settings import BaseSettings
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class Settings(BaseSettings):
    model_config = ConfigDict(env_file=".env", extra="ignore")

    app_env: str = "development"
    database_url: str = "postgresql+psycopg://shorturl:shorturl@localhost:5432/shorturl"
    redis_url: str = "redis://localhost:6379/0"
    secret_key: str = "dev-secret-key-change-in-production"
    geoip_db_path: str | None = None
    app_timezone: str = "Asia/Shanghai"
    trust_proxy_headers: bool = False
    maxmind_account_id: int | None = None
    maxmind_license_key: str | None = None
    ip_reputation_ttl_hours: int = 168
    maxmind_timeout_seconds: float = 1.5
    access_token_expire_minutes: int = 60 * 24
    cookie_secure: bool = False

    @field_validator("maxmind_account_id", "maxmind_license_key", mode="before")
    @classmethod
    def empty_maxmind_credentials_are_none(cls, value: object) -> object:
        """Treat Compose's empty optional MaxMind substitutions as absent."""
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("app_timezone")
    @classmethod
    def validate_app_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (TypeError, ZoneInfoNotFoundError) as exc:
            raise ValueError("必须是有效的 IANA 时区") from exc
        return value


settings = Settings()
