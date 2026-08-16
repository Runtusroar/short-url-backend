import base64
import binascii
import hashlib
import hmac
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from app.core.exceptions import APIError


MAX_CURSOR_LENGTH = 1024
_BASE64URL_RE = re.compile(r"^[A-Za-z0-9_-]+$")


class InvalidCursorError(APIError):
    def __init__(self) -> None:
        super().__init__(code="INVALID_CURSOR", message="无效的分页游标", status_code=400)


@dataclass(frozen=True)
class LogCursor:
    accessed_at: datetime
    id: UUID


def _base64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _base64url_decode(value: str) -> bytes:
    if not value or not _BASE64URL_RE.fullmatch(value) or len(value) % 4 == 1:
        raise ValueError("invalid base64url")
    padded = value + ("=" * (-len(value) % 4))
    decoded = base64.b64decode(padded.encode("ascii"), altchars=b"-_", validate=True)
    if _base64url_encode(decoded) != value:
        raise ValueError("non-canonical base64url")
    return decoded


def _canonical_payload(accessed_at: datetime, identifier: UUID, filter_digest: str) -> bytes:
    payload = {
        "v": 1,
        "at": accessed_at.isoformat(),
        "id": str(identifier),
        "f": filter_digest,
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode(
        "utf-8"
    )


def _validate_utc(accessed_at: datetime) -> None:
    if accessed_at.tzinfo is None or accessed_at.utcoffset() != timedelta(0):
        raise ValueError("cursor timestamp must be UTC")


def encode_cursor(position: LogCursor, filter_digest: str, secret_key: str) -> str:
    _validate_utc(position.accessed_at)
    payload = _canonical_payload(position.accessed_at, position.id, filter_digest)
    encoded_payload = _base64url_encode(payload)
    signature = hmac.new(
        secret_key.encode("utf-8"), encoded_payload.encode("ascii"), hashlib.sha256
    ).digest()
    return f"{encoded_payload}.{_base64url_encode(signature)}"


def decode_cursor(value: str, filter_digest: str, secret_key: str) -> LogCursor:
    try:
        if not isinstance(value, str) or len(value) > MAX_CURSOR_LENGTH:
            raise ValueError("cursor is too long")
        if value.count(".") != 1:
            raise ValueError("invalid cursor parts")
        encoded_payload, encoded_signature = value.split(".")
        payload_bytes = _base64url_decode(encoded_payload)
        signature = _base64url_decode(encoded_signature)
        expected_signature = hmac.new(
            secret_key.encode("utf-8"),
            encoded_payload.encode("ascii"),
            hashlib.sha256,
        ).digest()
        if not hmac.compare_digest(signature, expected_signature):
            raise ValueError("invalid signature")

        payload = json.loads(payload_bytes)
        if not isinstance(payload, dict) or set(payload) != {"v", "at", "id", "f"}:
            raise ValueError("invalid payload fields")
        if type(payload["v"]) is not int or payload["v"] != 1:
            raise ValueError("unsupported version")
        if not isinstance(payload["at"], str) or not isinstance(payload["id"], str):
            raise ValueError("invalid payload types")
        if not isinstance(payload["f"], str) or not hmac.compare_digest(
            payload["f"], filter_digest
        ):
            raise ValueError("invalid filter digest")

        accessed_at = datetime.fromisoformat(payload["at"])
        _validate_utc(accessed_at)
        if accessed_at.isoformat() != payload["at"]:
            raise ValueError("non-canonical timestamp")
        identifier = UUID(payload["id"])
        if str(identifier) != payload["id"]:
            raise ValueError("non-canonical UUID")
        if payload_bytes != _canonical_payload(accessed_at, identifier, payload["f"]):
            raise ValueError("non-canonical payload")
        return LogCursor(accessed_at=accessed_at, id=identifier)
    except (ValueError, TypeError, UnicodeError, binascii.Error, json.JSONDecodeError):
        raise InvalidCursorError() from None
