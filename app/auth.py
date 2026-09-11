"""Temporary compatibility exports for callers migrated in later tasks."""

from app.core.security import (
    create_access_token,
    decode_token,
    get_password_hash,
    oauth2_scheme,
    verify_password,
)
from app.dependencies import get_current_user, require_admin, require_role, require_staff

__all__ = [
    "create_access_token",
    "decode_token",
    "get_current_user",
    "get_password_hash",
    "oauth2_scheme",
    "require_admin",
    "require_role",
    "require_staff",
    "verify_password",
]
