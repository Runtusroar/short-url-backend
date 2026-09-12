from pydantic import ConfigDict, Field, field_validator, model_validator
from pydantic_settings import BaseSettings
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


_DEFAULT_SECRET_KEYS = frozenset(
    {
        "dev-secret-key-change-in-production",
        "change-me-to-a-random-secret-key-at-least-32-characters",
    }
)


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
    public_short_url_scheme: str = "http"
    public_short_url_port: int | None = Field(default=None, ge=1, le=65535)

    @field_validator("app_env")
    @classmethod
    def validate_app_env(cls, value: str) -> str:
        environment = value.strip().lower()
        if environment not in {"development", "test", "production"}:
            raise ValueError("APP_ENV 必须是 development、test 或 production")
        return environment

    @field_validator("public_short_url_scheme")
    @classmethod
    def validate_public_short_url_scheme(cls, value: str) -> str:
        scheme = value.strip().lower()
        if scheme not in {"http", "https"}:
            raise ValueError("PUBLIC_SHORT_URL_SCHEME 必须是 http 或 https")
        return scheme

    @field_validator(
        "maxmind_account_id",
        "maxmind_license_key",
        "public_short_url_port",
        mode="before",
    )
    @classmethod
    def empty_optional_settings_are_none(cls, value: object) -> object:
        """Treat empty optional environment substitutions as absent."""
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

    @model_validator(mode="after")
    def validate_production_security(self) -> "Settings":
        if self.app_env != "production":
            return self
        secret = self.secret_key.strip()
        if (
            not secret
            or secret in _DEFAULT_SECRET_KEYS
            or len(secret) < 32
        ):
            raise ValueError("生产环境 SECRET_KEY 必须是至少 32 字符且非默认的随机密钥")
        if not self.cookie_secure:
            raise ValueError("生产环境必须设置 COOKIE_SECURE=true")
        if self.public_short_url_scheme != "https":
            raise ValueError("生产环境必须设置 PUBLIC_SHORT_URL_SCHEME=https")
        return self


settings = Settings()
