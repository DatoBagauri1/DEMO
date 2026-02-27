from decimal import Decimal

import pytest
from django.utils import timezone

from planner.models import FxRate
from planner.services.fx import convert_minor_units, from_minor_units, to_minor_units


def test_to_minor_units_rounding_half_up():
    assert to_minor_units(Decimal("12.345")) == 1235
    assert to_minor_units(Decimal("12.344")) == 1234


@pytest.mark.django_db
def test_convert_minor_units_uses_saved_fx_rate():
    FxRate.objects.create(
        base_currency="EUR",
        quote_currency="USD",
        rate=Decimal("1.20000000"),
        as_of=timezone.now(),
        source="test",
    )
    amount_usd_minor = convert_minor_units(10_000, "EUR", "USD")
    assert amount_usd_minor == 12_000
    assert from_minor_units(amount_usd_minor) == Decimal("120")


@pytest.mark.django_db
def test_convert_minor_units_fallback_is_one_to_one_when_missing_rate():
    assert convert_minor_units(9_999, "JPY", "USD") == 9_999

