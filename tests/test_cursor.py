from datetime import datetime, timezone
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.services.cursor import CursorError, decode_cursor, encode_cursor


def test_cursor_round_trip_is_url_safe_and_preserves_the_log_position():
    """Changing the timestamp or id in a cursor must not silently move a log page."""
    accessed_at = datetime(2026, 9, 12, 8, 30, 45, 123456, tzinfo=timezone.utc)
    log_id = uuid4()

    token = encode_cursor(accessed_at, log_id)

    assert "+" not in token
    assert "/" not in token
    assert decode_cursor(token) == (accessed_at, log_id)


def test_cursor_rejects_a_validly_shaped_token_when_its_signature_is_tampered():
    """Changing a signed position must never let a caller select an arbitrary audit page."""
    token = encode_cursor(datetime(2026, 9, 12, tzinfo=timezone.utc), uuid4())
    replacement = "A" if token[-1] != "A" else "B"

    with pytest.raises(CursorError, match="INVALID_CURSOR"):
        decode_cursor(token[:-1] + replacement)


def test_settings_rejects_an_unknown_app_timezone_at_load_time():
    """A misspelled configured zone must fail startup instead of changing date boundaries at request time."""
    with pytest.raises(ValidationError, match="IANA"):
        Settings(app_timezone="Mars/Olympus_Mons")


@pytest.mark.parametrize("cursor", ["", "not-a-cursor", "eyJ0cyI6MX0", "a.b"])
def test_cursor_rejects_malformed_or_unsigned_values(cursor: str):
    """Accepting malformed or forged cursors could expose a different slice of audit history."""
    with pytest.raises(CursorError, match="INVALID_CURSOR"):
        decode_cursor(cursor)
