from pathlib import Path


DEPLOYMENT_DOC = Path(__file__).resolve().parents[2] / "docs" / "deployment.md"


def test_runbook_documents_proxy_intelligence_operations():
    text = DEPLOYMENT_DOC.read_text()

    for value in (
        "MAXMIND_INSIGHTS_ENABLED",
        "MAXMIND_ACCOUNT_ID",
        "MAXMIND_LICENSE_KEY",
        "MAXMIND_TIMEOUT_SECONDS",
        "fail-open",
        "proxy_error_code",
        "insufficient_funds",
        "redis_unavailable",
        "make migrate",
    ):
        assert value in text
    assert "GeoIP Insights" in text
    assert "not a readiness dependency" in text
