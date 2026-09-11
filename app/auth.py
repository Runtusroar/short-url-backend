"""Temporary compatibility exports for callers migrated in later tasks."""

from fastapi import Depends

from app.core.security import (
    create_access_token,
    decode_token,
    get_password_hash,
    oauth2_scheme,
    verify_password,
)
from app.dependencies import get_current_user, require_admin
from app.exceptions import PermissionDeniedError


def require_staff(current_user=Depends(get_current_user)):
    """Legacy-only staff check for routers removed in later tasks."""
    if current_user.role not in ("admin", "operator"):
        raise PermissionDeniedError("权限不足")
    return current_user

__all__ = [
    "create_access_token",
    "decode_token",
    "get_current_user",
    "get_password_hash",
    "oauth2_scheme",
    "require_admin",
    "require_staff",
    "verify_password",
]
