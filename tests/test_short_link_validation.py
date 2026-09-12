import pytest
from pydantic import ValidationError

from app.db.models import PolicyMode, TargetUrlType
from app.schemas.short_link import DestinationWrite, LinkPolicyWrite


@pytest.mark.parametrize(
    "url",
    [
        "/relative/path",
        "//example.com/protocol-relative",
        "javascript:alert(1)",
        "https://user:password@example.com/path",
        "https://example.com:0/path",
        "https://example.com:65536/path",
        "https://example.com/line\nbreak",
        "https://bad_name.example/path",
    ],
)
def test_destination_write_rejects_non_http_or_invalid_authorities(url):
    """An invalid destination could become an unsafe Location header at redirect time."""
    with pytest.raises(ValidationError):
        DestinationWrite(url=url, type=TargetUrlType.ALLOWED)


def test_destination_write_accepts_absolute_http_url_with_valid_port():
    """HTTPS destinations with a legal authority remain writable."""
    destination = DestinationWrite(url="https://example.com:8443/path?source=test", type="allowed")

    assert destination.url == "https://example.com:8443/path?source=test"


def test_platform_policy_uses_runtime_closed_values_and_deduplicates():
    """Unknown or duplicate values would create policy branches runtime UA detection cannot match."""
    policy = LinkPolicyWrite(
        platform_mode=PolicyMode.ALLOW,
        platforms=["DESKTOP", "desktop", "smartphone"],
    )

    assert policy.platforms == ["desktop", "smartphone"]
    with pytest.raises(ValidationError):
        LinkPolicyWrite(platform_mode=PolicyMode.ALLOW, platforms=["mobile"])
