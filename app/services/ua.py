from user_agents import parse


def get_platform(ua_string: str | None) -> str | None:
    if not ua_string:
        return None
    ua = parse(ua_string)
    if ua.is_mobile:
        return "mobile"
    if ua.is_tablet:
        return "tablet"
    if ua.is_pc:
        return "pc"
    if ua.is_bot:
        return "bot"
    return "other"


def parse_ua(ua_string: str | None) -> dict:
    if not ua_string:
        return {"platform": None, "browser": None, "os": None}
    ua = parse(ua_string)
    return {
        "platform": get_platform(ua_string),
        "browser": ua.browser.family,
        "os": ua.os.family,
    }
