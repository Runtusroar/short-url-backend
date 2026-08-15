"""Short-link service edge case tests."""

from app.features.short_links.short_code import validate_custom_alias


def test_custom_alias_reserved_prefix():
    assert validate_custom_alias("api") is False
    assert validate_custom_alias("admin") is False
    assert validate_custom_alias("openapi") is False


def test_custom_alias_too_short():
    assert validate_custom_alias("ab") is False
