from app.features.short_links.router import router
from app.features.short_links.schemas import (
    AccessRuleCreate,
    ShortLinkCreate,
    ShortLinkDetail,
    TargetUrlCreate,
)
from app.features.short_links.short_code import generate_short_code, validate_custom_alias


def test_short_link_feature_interfaces():
    assert router.prefix == "/api/short-links"
    assert ShortLinkCreate.model_fields["domain_id"].is_required()
    assert ShortLinkDetail.model_config["from_attributes"] is True
    assert AccessRuleCreate.model_fields["action"].is_required()
    assert TargetUrlCreate.model_fields["url"].is_required()
    assert len(generate_short_code()) == 6
    assert validate_custom_alias("abc") is True
