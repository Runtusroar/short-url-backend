import base64
import hashlib
import hmac
import json
from datetime import datetime, timezone
from uuid import UUID

import pytest

from app.features.access_logs.cursor import (
    InvalidCursorError,
    LogCursor,
    decode_cursor,
    encode_cursor,
)


POSITION = LogCursor(
    accessed_at=datetime(2026, 8, 16, 2, 30, tzinfo=timezone.utc),
    id=UUID("00000000-0000-0000-0000-000000000123"),
)


def tamper_payload(token: str) -> str:
    payload, signature = token.split(".")
    replacement = "A" if payload[0] != "A" else "B"
    return f"{replacement}{payload[1:]}.{signature}"


def tamper_signature(token: str) -> str:
    payload, signature = token.split(".")
    replacement = "A" if signature[0] != "A" else "B"
    return f"{payload}.{replacement}{signature[1:]}"


def truncate(token: str) -> str:
    return token[:-1]


def _signed_token(payload: dict, secret_key: str = "test-secret") -> str:
    raw_payload = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    encoded_payload = base64.urlsafe_b64encode(raw_payload).rstrip(b"=").decode()
    signature = hmac.new(
        secret_key.encode(), encoded_payload.encode(), hashlib.sha256
    ).digest()
    encoded_signature = base64.urlsafe_b64encode(signature).rstrip(b"=").decode()
    return f"{encoded_payload}.{encoded_signature}"


def _payload(**overrides: object) -> dict:
    payload = {
        "v": 1,
        "at": "2026-08-16T02:30:00+00:00",
        "id": "00000000-0000-0000-0000-000000000123",
        "f": "filters-v1",
    }
    payload.update(overrides)
    return payload


def test_signed_cursor_round_trip():
    encoded = encode_cursor(POSITION, "filters-v1", "test-secret")

    assert decode_cursor(encoded, "filters-v1", "test-secret") == POSITION


@pytest.mark.parametrize("mutation", [tamper_payload, tamper_signature, truncate])
def test_cursor_rejects_tampering(mutation):
    encoded = encode_cursor(POSITION, "filters-v1", "test-secret")

    with pytest.raises(InvalidCursorError):
        decode_cursor(mutation(encoded), "filters-v1", "test-secret")


@pytest.mark.parametrize(
    "token",
    [
        "not-base64!.signature",
        _signed_token({**_payload(), "extra": "nope"}),
        _signed_token(_payload(v=2)),
        _signed_token(_payload(at="2026-08-16T02:30:00")),
        _signed_token(_payload(at="2026-08-16T03:30:00+01:00")),
        _signed_token(_payload(id="not-a-uuid")),
        _signed_token(
            {
                "v": 1,
                "at": "2026-08-16T02:30:00+00:00",
                "id": "00000000-0000-0000-0000-000000000123",
            }
        ),
    ],
)
def test_cursor_rejects_malformed_or_non_utc_values(token):
    with pytest.raises(InvalidCursorError) as exc_info:
        decode_cursor(token, "filters-v1", "test-secret")

    assert exc_info.value.code == "INVALID_CURSOR"
    assert exc_info.value.message == "无效的分页游标"
    assert exc_info.value.status_code == 400
    assert token not in str(exc_info.value)


def test_cursor_rejects_oversized_input_before_accepting_a_valid_payload():
    filter_digest = "f" * 1000
    token = _signed_token(_payload(f=filter_digest))

    assert len(token) > 1024
    with pytest.raises(InvalidCursorError):
        decode_cursor(token, filter_digest, "test-secret")


def test_cursor_rejects_wrong_secret_or_filter_digest():
    token = encode_cursor(POSITION, "filters-v1", "test-secret")

    with pytest.raises(InvalidCursorError):
        decode_cursor(token, "filters-v1", "other-secret")
    with pytest.raises(InvalidCursorError):
        decode_cursor(token, "filters-v2", "test-secret")


def test_cursor_rejects_noncanonical_payload_encoding():
    raw_payload = b'{"v":1, "at":"2026-08-16T02:30:00+00:00","id":"00000000-0000-0000-0000-000000000123","f":"filters-v1"}'
    encoded_payload = base64.urlsafe_b64encode(raw_payload).rstrip(b"=").decode()
    signature = hmac.new(
        b"test-secret", encoded_payload.encode(), hashlib.sha256
    ).digest()
    token = f"{encoded_payload}.{base64.urlsafe_b64encode(signature).rstrip(b'=').decode()}"

    with pytest.raises(InvalidCursorError):
        decode_cursor(token, "filters-v1", "test-secret")


def test_cursor_errors_do_not_echo_untrusted_input():
    token = "secret-cursor-input"

    with pytest.raises(InvalidCursorError) as exc_info:
        decode_cursor(token, "filters-v1", "test-secret")

    assert str(exc_info.value) == "无效的分页游标"
