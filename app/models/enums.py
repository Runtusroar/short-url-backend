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


class ProxyErrorCode(StrEnum):
    DISABLED = "disabled"
    REDIS_UNAVAILABLE = "redis_unavailable"
    AUTH_FAILED = "auth_failed"
    INSUFFICIENT_FUNDS = "insufficient_funds"
    PERMISSION_DENIED = "permission_denied"
    RATE_LIMITED = "rate_limited"
    TIMEOUT = "timeout"
    UPSTREAM_ERROR = "upstream_error"
    INVALID_RESPONSE = "invalid_response"
    IP_NOT_FOUND = "ip_not_found"
    INVALID_IP = "invalid_ip"
    NON_GLOBAL_IP = "non_global_ip"
    LOOKUP_CONTENDED = "lookup_contended"
