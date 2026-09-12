import pytest
from pydantic import ValidationError

from app.schemas.domain import DomainCreate
from app.services.request_metadata import parse_authority


def test_domain_schema_and_request_authority_share_dns_normalization():
    """Divergent host parsers could route a request to a domain the API cannot store."""
    assert DomainCreate(name="Example.COM.").name == "example.com"
    assert parse_authority("Example.COM.:443").host == "example.com"


@pytest.mark.parametrize(
    "value",
    [
        "",
        "bad..example",
        "-bad.example",
        "bad-.example",
        "bad_name.example",
        "https://example.com",
        "user@example.com",
        "example.com/path",
        "例子.example",
        "K.example",
        " example.com",
        "example.com ",
        "127.0.0.1",
    ],
)
def test_domain_schema_rejects_non_dns_hostnames(value):
    """Accepting non-DNS names would make stored tenants disagree with Host routing."""
    with pytest.raises(ValidationError):
        DomainCreate(name=value)


@pytest.mark.parametrize(
    "value",
    [
        "bad..example",
        "bad_name.example",
        "user@example.com",
        "example.com/path",
        "K.example",
        " example.com",
        "example.com ",
        "127.0.0.1",
        "example.com:0",
        "example.com:65536",
        "example.com:not-a-port",
    ],
)
def test_request_authority_rejects_malformed_or_non_dns_hosts(value):
    """Malformed Host syntax must not select a tenant or enter an audit snapshot."""
    assert parse_authority(value) is None
