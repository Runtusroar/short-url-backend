from pathlib import Path


DOC = Path(__file__).resolve().parents[2] / "docs" / "deployment.md"


def test_runbook_documents_access_log_search_and_cursor_continuation():
    text = DOC.read_text()
    compact = " ".join(text.split())

    for required_text in (
        "GET /api/logs",
        "short_code",
        "name",
        "date_from",
        "date_to",
        "country=CN&country=US",
        "result=allowed&result=denied",
        "next_cursor",
        "has_more",
        "accessed_at DESC, id DESC",
    ):
        assert required_text in text

    assert "Short codes are stored in canonical lowercase" in compact
    assert "request paths are case-insensitive at the application boundary" in compact
    assert "Cursor values are opaque" in compact
    assert "filters must not change while following a cursor" in compact
    assert "Operator and client results are permission-scoped" in compact
