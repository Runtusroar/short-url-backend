from app.services.user_agent import parse_user_agent


def test_parse_mobile_browser_with_model():
    parsed = parse_user_agent(
        "Mozilla/5.0 (Linux; Android 13; SM-S918B) "
        "AppleWebKit/537.36 Chrome/120.0.0.0 Mobile Safari/537.36"
    )

    assert parsed.browser == "Chrome Mobile"
    assert parsed.os == "Android"
    assert parsed.device_type == "smartphone"
    assert parsed.brand == "Samsung"
    assert parsed.model is not None
    assert parsed.is_bot is False


def test_parse_googlebot():
    parsed = parse_user_agent(
        "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"
    )

    assert parsed.is_bot is True
    assert parsed.platform == "bot"
    assert parsed.bot_name == "Googlebot"


def test_parse_missing_ua_is_stable():
    parsed = parse_user_agent(None)

    assert parsed.platform == "other"
    assert parsed.raw is None
