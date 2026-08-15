import geoip2.database
import geoip2.errors

from app.core.config import settings

_geoip_reader: geoip2.database.Reader | None = None


def get_geoip_reader() -> geoip2.database.Reader | None:
    global _geoip_reader
    if _geoip_reader is None and settings.geoip_db_path:
        try:
            _geoip_reader = geoip2.database.Reader(settings.geoip_db_path)
        except (FileNotFoundError, geoip2.errors.GeoIP2Error):
            return None
    return _geoip_reader


def get_country(ip: str) -> str | None:
    reader = get_geoip_reader()
    if not reader:
        return None
    try:
        db_type = reader.metadata().database_type
        if "Country" in db_type:
            response = reader.country(ip)
        else:
            response = reader.city(ip)
        return response.country.iso_code
    except (geoip2.errors.AddressNotFoundError, ValueError):
        return None
