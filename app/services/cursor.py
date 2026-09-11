"""Signed cursor encoding for descending access-log pagination."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
from datetime import datetime, timezone
from uuid import UUID

from app.core.config import settings


class CursorError(ValueError):
    """Raised when an opaque pagination cursor is malformed or has been altered."""

    def __init__(self) -> None:
        super().__init__("INVALID_CURSOR")


_URLSAFE_TOKEN = re.compile(r"^[A-Za-z0-9_-]+$")


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _decode(value: str) -> bytes:
    if not value or _URLSAFE_TOKEN.fullmatch(value) is None:
        raise CursorError()
    try:
        return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except (ValueError, UnicodeError) as exc:
        raise CursorError() from exc


def _payload(accessed_at: datetime, log_id: UUID) -> bytes:
    if accessed_at.tzinfo is None or accessed_at.utcoffset() is None:
        raise ValueError("Cursor timestamps must be timezone-aware")
    timestamp = accessed_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    return json.dumps({"id": str(log_id), "timestamp": timestamp}, separators=(",", ":"), sort_keys=True).encode(
        "utf-8"
    )


def _signature(payload: bytes) -> bytes:
    return hmac.new(settings.secret_key.encode("utf-8"), payload, hashlib.sha256).digest()


def encode_cursor(accessed_at: datetime, log_id: UUID) -> str:
    """Return a compact URL-safe cursor whose timestamp and UUID are authenticated."""
    payload = _payload(accessed_at, log_id)
    return f"{_encode(payload)}.{_encode(_signature(payload))}"


def decode_cursor(cursor: str) -> tuple[datetime, UUID]:
    """Validate and decode a cursor, normalizing its timestamp to UTC."""
    if not isinstance(cursor, str) or cursor.count(".") != 1:
        raise CursorError()
    encoded_payload, encoded_signature = cursor.split(".")
    payload = _decode(encoded_payload)
    signature = _decode(encoded_signature)
    if not hmac.compare_digest(signature, _signature(payload)):
        raise CursorError()
    try:
        value = json.loads(payload)
        if set(value) != {"id", "timestamp"} or not isinstance(value["id"], str) or not isinstance(value["timestamp"], str):
            raise ValueError
        accessed_at = datetime.fromisoformat(value["timestamp"].replace("Z", "+00:00"))
        if accessed_at.tzinfo is None or accessed_at.utcoffset() is None:
            raise ValueError
        return accessed_at.astimezone(timezone.utc), UUID(value["id"])
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise CursorError() from exc
