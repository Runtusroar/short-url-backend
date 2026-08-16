"""Sanitized adapter for the paid MaxMind Insights service."""

from dataclasses import dataclass
from enum import StrEnum

import aiohttp
import geoip2.errors
import geoip2.webservice

ANONYMIZER_FIELDS = (
    ("is_anonymous_vpn", "anonymous_vpn"),
    ("is_hosting_provider", "hosting_provider"),
    ("is_public_proxy", "public_proxy"),
    ("is_residential_proxy", "residential_proxy"),
    ("is_tor_exit_node", "tor_exit_node"),
)


class InsightsErrorKind(StrEnum):
    AUTH_FAILED = "auth_failed"
    INSUFFICIENT_FUNDS = "insufficient_funds"
    PERMISSION_DENIED = "permission_denied"
    RATE_LIMITED = "rate_limited"
    TIMEOUT = "timeout"
    UPSTREAM_ERROR = "upstream_error"
    INVALID_RESPONSE = "invalid_response"
    IP_NOT_FOUND = "ip_not_found"


class InsightsLookupError(RuntimeError):
    def __init__(self, kind: InsightsErrorKind):
        super().__init__(kind.value)
        self.kind = kind


@dataclass(frozen=True)
class InsightsResult:
    is_anonymous: bool
    proxy_types: tuple[str, ...]


class MaxMindInsightsClient:
    def __init__(self, *, account_id: int, license_key: str, timeout: float):
        self._client = geoip2.webservice.AsyncClient(
            account_id=account_id,
            license_key=license_key,
            timeout=timeout,
        )

    async def lookup(self, ip_address: str) -> InsightsResult:
        try:
            record = await self._client.insights(ip_address)
        except geoip2.errors.AuthenticationError:
            raise InsightsLookupError(InsightsErrorKind.AUTH_FAILED) from None
        except geoip2.errors.OutOfQueriesError:
            raise InsightsLookupError(InsightsErrorKind.INSUFFICIENT_FUNDS) from None
        except geoip2.errors.PermissionRequiredError:
            raise InsightsLookupError(InsightsErrorKind.PERMISSION_DENIED) from None
        except geoip2.errors.AddressNotFoundError:
            raise InsightsLookupError(InsightsErrorKind.IP_NOT_FOUND) from None
        except geoip2.errors.InvalidRequestError:
            # assess_proxy supplies a canonical global IP, so an unrecognized
            # 4xx service code is response-contract drift at this boundary.
            raise InsightsLookupError(InsightsErrorKind.INVALID_RESPONSE) from None
        except TimeoutError:
            raise InsightsLookupError(InsightsErrorKind.TIMEOUT) from None
        except aiohttp.ClientError:
            raise InsightsLookupError(InsightsErrorKind.UPSTREAM_ERROR) from None
        except geoip2.errors.HTTPError as exc:
            kind = (
                InsightsErrorKind.RATE_LIMITED
                if exc.http_status == 429
                else InsightsErrorKind.UPSTREAM_ERROR
            )
            raise InsightsLookupError(kind) from None
        except geoip2.errors.GeoIP2Error:
            # geoip2 4.8 uses the base error for undecodable successful JSON.
            raise InsightsLookupError(InsightsErrorKind.INVALID_RESPONSE) from None

        try:
            raw = record.raw
            anonymizer = raw["anonymizer"]
            if not isinstance(anonymizer, dict):
                raise TypeError
            is_anonymous = anonymizer.get("is_anonymous", False)
            proxy_values = tuple(
                (anonymizer.get(field, False), proxy_type)
                for field, proxy_type in ANONYMIZER_FIELDS
            )
            if not isinstance(is_anonymous, bool) or any(
                not isinstance(value, bool) for value, _ in proxy_values
            ):
                raise TypeError
            proxy_types = tuple(
                proxy_type for value, proxy_type in proxy_values if value
            )
        except (AttributeError, KeyError, TypeError):
            raise InsightsLookupError(InsightsErrorKind.INVALID_RESPONSE) from None

        return InsightsResult(is_anonymous=is_anonymous, proxy_types=proxy_types)

    async def close(self) -> None:
        await self._client.close()
