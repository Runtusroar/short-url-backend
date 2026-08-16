from dataclasses import replace
from datetime import date
from types import SimpleNamespace
from uuid import UUID

import pytest
from pydantic import ValidationError

from app.features.access_logs.query import AccessLogFilters
from app.features.access_logs.schemas import AccessLogFilterParams
from app.models.enums import RedirectResult


def test_filters_trim_normalize_sort_and_deduplicate():
    filters = AccessLogFilters.from_values(
        short_link_id=None,
        short_code=" Promo_ ",
        name="  八月 推广  ",
        date_from=date(2026, 8, 1),
        date_to=date(2026, 8, 7),
        countries=["us", "CN", "US"],
        results=["denied", "allowed", "denied"],
    )

    assert filters.short_code == "promo_"
    assert filters.name == "八月 推广"
    assert filters.countries == ("CN", "US")
    assert filters.results == (RedirectResult.ALLOWED, RedirectResult.DENIED)


def test_filter_digest_binds_user_domain_and_filters_but_not_limit():
    domain_id = UUID("00000000-0000-0000-0000-000000000010")
    other_domain_id = UUID("00000000-0000-0000-0000-000000000011")
    user = SimpleNamespace(
        id=UUID("00000000-0000-0000-0000-000000000020"), role="operator"
    )
    other_user = SimpleNamespace(
        id=UUID("00000000-0000-0000-0000-000000000021"), role="operator"
    )
    filters = AccessLogFilters.from_values(
        short_link_id=None,
        short_code="promo",
        name="August",
        date_from=date(2026, 8, 1),
        date_to=date(2026, 8, 7),
        countries=["CN", "US"],
        results=["allowed", "denied"],
    )
    equivalent_filters = AccessLogFilters.from_values(
        short_link_id=None,
        short_code=" PROMO ",
        name=" August ",
        date_from=date(2026, 8, 1),
        date_to=date(2026, 8, 7),
        countries=["US", "CN", "US"],
        results=["denied", "allowed"],
    )
    changed_name = replace(filters, name="September")

    first = filters.digest_scope(domain_id, user)

    assert first == equivalent_filters.digest_scope(domain_id, user)
    assert first != changed_name.digest_scope(domain_id, user)
    assert first != filters.digest_scope(other_domain_id, user)
    assert first != filters.digest_scope(domain_id, other_user)


@pytest.mark.parametrize(
    ("params", "field"),
    [
        ({"name": "   "}, "name"),
        ({"name": "x" * 129}, "name"),
        ({"short_code": "promo!"}, "short_code"),
        ({"date_from": "2026-08-08", "date_to": "2026-08-07"}, ""),
        ({"countries": ["ZZ"]}, "countries"),
        ({"results": ["unknown"]}, "results"),
    ],
)
def test_filter_params_reject_invalid_query_values(params, field):
    with pytest.raises(ValidationError) as exc_info:
        AccessLogFilterParams(**params)

    assert field in str(exc_info.value)


def test_filter_params_normalize_before_converting_to_frozen_filters():
    params = AccessLogFilterParams(
        short_code=" Promo_ ",
        name=" August ",
        countries=["us", "CN", "US"],
        results=["denied", "allowed", "denied"],
    )

    filters = params.to_filters()

    assert filters.short_code == "promo_"
    assert filters.name == "August"
    assert filters.countries == ("CN", "US")
    assert filters.results == (RedirectResult.ALLOWED, RedirectResult.DENIED)
    with pytest.raises(AttributeError):
        filters.name = "changed"
