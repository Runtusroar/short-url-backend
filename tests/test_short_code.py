from app.features.short_links.short_code import generate_short_code, validate_custom_alias


def test_generate_short_code_length():
    code = generate_short_code()
    assert len(code) == 6


def test_generate_short_code_charset():
    code = generate_short_code()
    assert code.isalnum()


def test_validate_custom_alias_ok():
    assert validate_custom_alias("my-link_123") is True


def test_validate_custom_alias_too_short():
    assert validate_custom_alias("ab") is False


def test_validate_custom_alias_reserved():
    assert validate_custom_alias("api") is False
    assert validate_custom_alias("API") is False
