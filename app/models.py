"""Temporary compatibility re-exports for callers migrated in later tasks."""

from app.db.models import (  # noqa: F401
    AccessLevel,
    AccessLog,
    AccessResult,
    BlockReason,
    Domain,
    IpBlacklist,
    IpReputation,
    LinkPolicy,
    PolicyMode,
    ShortLink,
    TargetUrl,
    TargetUrlType,
    User,
    UserDomainAccess,
    UserRole,
)

# Legacy route modules are replaced in later tasks.  These aliases only keep
# their imports loadable while the compatibility module exists.
UserDomain = UserDomainAccess
ShortLinkPermission = UserDomainAccess
AccessRule = LinkPolicy
