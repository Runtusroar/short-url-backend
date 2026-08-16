from enum import StrEnum


class UserRole(StrEnum):
    ADMIN = "admin"
    OPERATOR = "operator"
    CLIENT = "client"


class AccessAction(StrEnum):
    ALLOW = "allow"
    DENY = "deny"


class ClientRequirement(StrEnum):
    ANY = "any"
    HUMAN = "human"
    BOT = "bot"


class ProxyRequirement(StrEnum):
    ANY = "any"
    NON_PROXY = "non_proxy"
    PROXY = "proxy"


class RedirectResult(StrEnum):
    ALLOWED = "allowed"
    DENIED = "denied"
    BLOCKED = "blocked"


class DecisionReason(StrEnum):
    LEGACY_UNKNOWN = "legacy_unknown"
    BLACKLIST = "blacklist"
    MATCHED_RULE = "matched_rule"
    DEFAULT_ACTION = "default_action"
    NO_TARGET = "no_target"


class ProxyCheckStatus(StrEnum):
    SKIPPED = "skipped"
    CACHED = "cached"
    CHECKED = "checked"
    ASSUMED_BOT = "assumed_bot"
    ERROR = "error"


class ProxySource(StrEnum):
    MAXMIND_INSIGHTS = "maxmind_insights"
    ASSUMED_BOT = "assumed_bot"
