from enum import StrEnum


class UserRole(StrEnum):
    ADMIN = "admin"
    SUBACCOUNT = "subaccount"


class AccessLevel(StrEnum):
    READ = "read"
    MANAGE = "manage"


class TargetUrlType(StrEnum):
    ALLOWED = "allowed"
    BLOCKED = "blocked"


class PolicyMode(StrEnum):
    OFF = "off"
    ALLOW = "allow"
    BLOCK = "block"


class Platform(StrEnum):
    DESKTOP = "desktop"
    SMARTPHONE = "smartphone"
    TABLET = "tablet"
    TV = "tv"
    CONSOLE = "console"
    WEARABLE = "wearable"
    BOT = "bot"
    OTHER = "other"


class AccessResult(StrEnum):
    ALLOWED = "allowed"
    BLOCKED = "blocked"
    ERROR = "error"


class BlockReason(StrEnum):
    IP = "ip"
    PROXY = "proxy"
    COUNTRY = "country"
    BOT = "bot"
    PLATFORM = "platform"
    REFERER = "referer"
    OTHER = "other"
