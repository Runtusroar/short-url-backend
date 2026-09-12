import uuid

import pytest

from app.services.short_code import create_unique_short_code, generate_short_code, validate_custom_alias


def test_generate_short_code_length():
    code = generate_short_code()
    assert len(code) == 6


def test_generate_short_code_charset():
    code = generate_short_code()
    assert code.isalnum()
    assert code == code.lower()


def test_validate_custom_alias_ok():
    assert validate_custom_alias("my-link_123") is True


def test_validate_custom_alias_too_short():
    assert validate_custom_alias("ab") is False


def test_validate_custom_alias_reserved():
    assert validate_custom_alias("api") is False
    assert validate_custom_alias("API") is False


@pytest.mark.parametrize("path", ["api", "admin", "static", "health", "docs", "redoc", "openapi"])
def test_validate_custom_alias_rejects_every_fixed_root_path(path):
    assert validate_custom_alias(path) is False
    assert validate_custom_alias(path.upper()) is False


@pytest.mark.asyncio
async def test_generated_codes_skip_the_same_fixed_root_paths_as_custom_aliases(db, monkeypatch):
    """A random collision with a root route must be retried just like a custom alias collision."""
    generated = iter(["health", "safe42"])
    monkeypatch.setattr("app.services.short_code.generate_short_code", lambda: next(generated))

    code = await create_unique_short_code(db, uuid.uuid4())

    assert code == "safe42"


def test_validate_custom_alias_rejects_a_trailing_newline():
    """A partial regex match would persist an alias that cannot safely be routed."""
    assert validate_custom_alias("campaign-a\n") is False
