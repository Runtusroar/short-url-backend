from app.features.redirect.router import router
from app.features.redirect.service import evaluate_rules, weighted_random_choice
from app.features.redirect.ua import get_platform
from app.integrations.maxmind.country import get_country
from app.integrations.redis import close_redis_client, create_redis_client


def test_redirect_and_integration_interfaces():
    assert router.prefix == ""
    assert callable(evaluate_rules)
    assert callable(weighted_random_choice)
    assert callable(get_platform)
    assert callable(get_country)
    assert callable(create_redis_client)
    assert callable(close_redis_client)
