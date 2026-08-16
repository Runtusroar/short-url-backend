import ast
import inspect
from importlib import import_module
from pathlib import Path

from app.features.redirect.router import router
from app.features.redirect.service import evaluate_rules, weighted_random_choice
from app.features.redirect.ua import get_platform
from app.integrations.maxmind.country import get_country
from app.integrations.maxmind.insights import MaxMindInsightsClient
from app.integrations.redis import close_redis_client, create_redis_client


ROOT = Path(__file__).resolve().parents[2]


def test_redirect_and_integration_interfaces():
    assert router.prefix == ""
    assert callable(evaluate_rules)
    assert callable(weighted_random_choice)
    assert callable(get_platform)
    assert callable(get_country)
    assert callable(MaxMindInsightsClient)
    assert callable(create_redis_client)
    assert callable(close_redis_client)


def test_redirect_service_exposes_one_request_workflow():
    service = import_module("app.features.redirect.service")
    workflow = getattr(service, "execute_redirect", None)

    assert inspect.iscoroutinefunction(workflow)
    assert list(inspect.signature(workflow).parameters) == [
        "db",
        "host",
        "short_code",
            "client_ip",
            "ua_string",
            "referer",
            "request_method",
        ]


def test_redirect_router_only_translates_http_around_the_workflow():
    tree = ast.parse(ROOT.joinpath("app/features/redirect/router.py").read_text())
    imports = [node for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
    service_names = {
        alias.name
        for node in imports
        if node.module == "app.features.redirect.service"
        for alias in node.names
    }
    imported_modules = {node.module for node in imports}

    assert service_names == {"execute_redirect"}
    assert "app.features.redirect.ua" not in imported_modules
    assert "app.integrations.maxmind.country" not in imported_modules
    assert "app.core.exceptions" not in imported_modules
