from pathlib import Path


DOC = Path(__file__).resolve().parents[1] / "docs" / "deployment.md"


def test_nginx_overwrites_single_proxy_headers():
    text = DOC.read_text()
    assert "proxy_pass http://127.0.0.1:18000;" in text
    assert "proxy_set_header Host $host;" in text
    assert "proxy_set_header X-Real-IP $remote_addr;" in text
    assert "proxy_set_header X-Forwarded-For $remote_addr;" in text
    assert "proxy_set_header X-Forwarded-Proto $scheme;" in text
    assert "$proxy_add_x_forwarded_for" not in text


def test_runbook_uses_makefile_entrypoints():
    text = DOC.read_text()
    for command in (
        "make check-config",
        "make up",
        "make migrate",
        "make logs",
        "make restart",
    ):
        assert command in text
