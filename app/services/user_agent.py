from dataclasses import dataclass
from typing import Mapping

from device_detector import DeviceDetector

from app.db.models import Platform


@dataclass(frozen=True, slots=True)
class ParsedUserAgent:
    raw: str | None
    platform: Platform
    browser: str | None
    browser_version: str | None
    os: str | None
    os_version: str | None
    device_type: str | None
    brand: str | None
    model: str | None
    is_bot: bool
    bot_name: str | None


def _normalize_platform(device_type: str | None) -> Platform:
    return {
        "desktop": Platform.DESKTOP,
        "smartphone": Platform.SMARTPHONE,
        "tablet": Platform.TABLET,
        "tv": Platform.TV,
        "console": Platform.CONSOLE,
        "wearable": Platform.WEARABLE,
    }.get(device_type or "", Platform.OTHER)


def parse_user_agent(
    ua: str | None,
    headers: Mapping[str, str] | None = None,
) -> ParsedUserAgent:
    if not ua:
        return ParsedUserAgent(
            None,
            Platform.OTHER,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            False,
            None,
        )

    detector = DeviceDetector(ua, headers=dict(headers or {})).parse()
    is_bot = detector.is_bot()
    detected_device_type = detector.device_type()
    device_type = str(detected_device_type) if detected_device_type else None

    return ParsedUserAgent(
        raw=ua,
        platform=Platform.BOT if is_bot else _normalize_platform(device_type),
        browser=detector.client_name() or None,
        browser_version=detector.client_version() or None,
        os=detector.os_name() or None,
        os_version=detector.os_version() or None,
        device_type=device_type,
        brand=detector.device_brand() or None,
        model=detector.device_model() or None,
        is_bot=is_bot,
        bot_name=(detector.bot.name() or None) if is_bot else None,
    )
