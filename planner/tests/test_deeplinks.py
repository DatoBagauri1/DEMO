from __future__ import annotations

from datetime import date

import pytest

from planner.services.deeplinks import build_flight_search_link


@pytest.mark.django_db
def test_build_flight_search_link_omits_cabin_parameter():
    url = build_flight_search_link(
        origin="TBS",
        destination="JFK",
        depart_date=date(2026, 7, 10),
        return_date=date(2026, 7, 17),
        travelers=2,
        cabin="business",
        plan_id="plan123",
    )

    assert "origin=TBS" in url
    assert "destination=JFK" in url
    assert "adults=2" in url
    assert "cabin=" not in url


@pytest.mark.django_db
def test_build_flight_search_link_keeps_return_date_when_available():
    url = build_flight_search_link(
        origin="TBS",
        destination="JFK",
        depart_date=date(2026, 7, 10),
        return_date=date(2026, 7, 17),
        travelers=1,
        plan_id="plan123",
    )

    assert "depart_date=2026-07-10" in url
    assert "return_date=2026-07-17" in url


@pytest.mark.django_db
def test_build_flight_search_link_handles_one_way_without_return_date():
    url = build_flight_search_link(
        origin="TBS",
        destination="JFK",
        depart_date=date(2026, 7, 10),
        return_date=None,
        travelers=1,
        plan_id="plan123",
    )

    assert "depart_date=2026-07-10" in url
    assert "return_date=" not in url
