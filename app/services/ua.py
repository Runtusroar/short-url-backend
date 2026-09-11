from app.services.user_agent import parse_user_agent


def get_platform(ua_string: str | None) -> str | None:
    if not ua_string:
        return None
    platform = parse_user_agent(ua_string).platform
    if platform == "smartphone":
        return "mobile"
    if platform == "tablet":
        return "tablet"
    if platform == "desktop":
        return "pc"
    if platform == "bot":
        return "bot"
    return "other"

