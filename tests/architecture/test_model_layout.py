from importlib import import_module

from app.core.database import Base
from app.models import (
    AccessLog,
    AccessRule,
    Domain,
    IpBlacklist,
    ShortLink,
    ShortLinkPermission,
    TargetUrl,
    User,
    UserDomain,
)


def test_models_live_in_focused_modules_and_are_reexported():
    expected = {
        "user": User,
        "domain": Domain,
        "short_link": ShortLink,
        "access_rule": AccessRule,
        "blacklist": IpBlacklist,
        "access_log": AccessLog,
    }
    for module_name, model in expected.items():
        assert getattr(import_module(f"app.models.{module_name}"), model.__name__) is model


def test_table_names_are_unchanged():
    assert set(Base.metadata.tables) == {
        "users",
        "domains",
        "user_domains",
        "short_links",
        "short_link_permissions",
        "target_urls",
        "access_rules",
        "ip_blacklist",
        "access_logs",
    }
    assert ShortLinkPermission.__tablename__ == "short_link_permissions"
    assert TargetUrl.__tablename__ == "target_urls"
    assert UserDomain.__tablename__ == "user_domains"
