from app.features.access_logs.router import router
from app.features.access_logs.schemas import AccessLogResponse, DailyStatsResponse
from app.features.access_logs.service import can_view_link, resolve_effective_domain


def test_access_log_feature_interfaces():
    assert router.prefix == "/api/logs"
    assert AccessLogResponse.model_config["from_attributes"] is True
    assert DailyStatsResponse.model_fields["unique_ips"].is_required()
    assert callable(can_view_link)
    assert callable(resolve_effective_domain)
