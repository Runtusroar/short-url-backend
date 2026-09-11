import random
import re
import string
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ShortLink

CHARSET = string.ascii_lowercase + string.digits
CODE_LENGTH = 6
MAX_RETRIES = 5
CUSTOM_ALIAS_RE = re.compile(r"^[a-zA-Z0-9_-]{3,32}$")
RESERVED_PREFIXES = {"api", "admin", "static", "docs", "openapi"}


def generate_short_code(length: int = CODE_LENGTH) -> str:
    return "".join(random.choices(CHARSET, k=length))


def validate_custom_alias(alias: str) -> bool:
    if not CUSTOM_ALIAS_RE.match(alias):
        return False
    if alias.lower() in RESERVED_PREFIXES:
        return False
    return True


async def create_unique_short_code(
    db: AsyncSession,
    domain_id: UUID,
    custom_alias: str | None = None,
    *,
    exclude_link_id: UUID | None = None,
) -> str:
    if custom_alias:
        if not validate_custom_alias(custom_alias):
            raise ValueError("Invalid custom alias")
        conditions = [ShortLink.domain_id == domain_id, ShortLink.short_code == custom_alias]
        if exclude_link_id is not None:
            conditions.append(ShortLink.id != exclude_link_id)
        existing = await db.execute(select(ShortLink).where(*conditions))
        if existing.scalar_one_or_none():
            raise ValueError("Custom alias already exists in this domain")
        return custom_alias

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
