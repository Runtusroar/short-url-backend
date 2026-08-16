import re
import secrets
import string
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ShortLink

CHARSET = string.ascii_lowercase + string.digits
CODE_LENGTH = 7
MAX_RETRIES = 5
CUSTOM_ALIAS_RE = re.compile(r"^[a-z0-9_-]{3,32}$")
RESERVED_PREFIXES = {"api", "admin", "static", "docs", "openapi"}


def normalize_short_code(value: str) -> str:
    return value.strip().lower()


def generate_short_code(length: int = CODE_LENGTH) -> str:
    return "".join(secrets.choice(CHARSET) for _ in range(length))


def validate_custom_alias(alias: str) -> bool:
    if not CUSTOM_ALIAS_RE.match(alias):
        return False
    if alias.lower() in RESERVED_PREFIXES:
        return False
    return True


async def create_unique_short_code(
    db: AsyncSession, domain_id: UUID, custom_alias: str | None = None
) -> str:
    if custom_alias:
        normalized_alias = normalize_short_code(custom_alias)
        if not validate_custom_alias(normalized_alias):
            raise ValueError("Invalid custom alias")
        existing = await db.execute(
            select(ShortLink).where(
                ShortLink.domain_id == domain_id,
                ShortLink.short_code == normalized_alias,
            )
        )
        if existing.scalar_one_or_none():
            raise ValueError("Custom alias already exists in this domain")
        return normalized_alias

    for _ in range(MAX_RETRIES):
        code = generate_short_code()
        existing = await db.execute(
            select(ShortLink).where(
                ShortLink.domain_id == domain_id,
                ShortLink.short_code == code,
            )
        )
        if not existing.scalar_one_or_none():
            return code
    raise RuntimeError("Failed to generate unique short code")
