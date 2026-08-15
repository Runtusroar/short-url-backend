from app.features.blacklist.router import router as blacklist_router
from app.features.domains.dependencies import get_request_host
from app.features.domains.router import router as domains_router
from app.features.domains.schemas import DomainCreate, DomainResponse


def test_domain_and_blacklist_feature_interfaces():
    assert domains_router.prefix == "/api/domains"
    assert blacklist_router.prefix == "/api/ip-blacklist"
    assert callable(get_request_host)
    assert DomainCreate.model_fields["name"].is_required()
    assert DomainResponse.model_config["from_attributes"] is True
