from unittest.mock import MagicMock, patch

from app.services.geoip import get_country, get_geoip_reader


def test_geoip_without_configured_reader_returns_unknown_country():
    with patch("app.services.geoip.settings") as configured:
        configured.geoip_db_path = None
        assert get_geoip_reader() is None
        assert get_country("8.8.8.8") is None


def test_geoip_invalid_ip_returns_unknown_country():
    reader = MagicMock()
    reader.city.side_effect = ValueError("invalid ip")
    with patch("app.services.geoip.get_geoip_reader", return_value=reader):
        assert get_country("not-an-ip") is None
