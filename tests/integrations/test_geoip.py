"""GeoIP adapter edge case tests."""

from unittest.mock import MagicMock, patch

from app.integrations.maxmind.country import get_country, get_geoip_reader


def test_geoip_without_reader():
    with patch("app.integrations.maxmind.country.settings") as mock_settings:
        mock_settings.geoip_db_path = None
        assert get_geoip_reader() is None
        assert get_country("8.8.8.8") is None


def test_geoip_invalid_ip():
    reader = MagicMock()
    reader.city.side_effect = ValueError("invalid ip")
    with patch("app.integrations.maxmind.country.get_geoip_reader", return_value=reader):
        assert get_country("not-an-ip") is None
