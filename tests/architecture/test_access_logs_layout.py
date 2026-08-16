from app.features.access_logs.router import router
from app.features.access_logs.schemas import (
    AccessLogPageResponse,
    AccessLogResponse,
    DailyStatsResponse,
)
from app.features.access_logs.service import can_view_link, resolve_effective_domain


def test_access_log_feature_interfaces():
    assert router.prefix == "/api/logs"
    assert AccessLogResponse.model_config["from_attributes"] is True
    assert DailyStatsResponse.model_fields["unique_ips"].is_required()
    assert callable(can_view_link)
    assert callable(resolve_effective_domain)


def test_access_log_route_response_models_preserve_daily_contracts():
    response_models = {
        route.name: route.response_model
        for route in router.routes
        if route.name in {"list_logs", "daily_stats", "daily_summary"}
    }
    assert response_models == {
        "list_logs": AccessLogPageResponse,
        "daily_stats": list[DailyStatsResponse],
        "daily_summary": list[DailyStatsResponse],
    }
