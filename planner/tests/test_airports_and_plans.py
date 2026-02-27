from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.contrib.auth.models import User
from django.utils import timezone
from rest_framework.test import APIClient

from planner.models import Airport, DestinationCandidate, FlightOption, HotelOption, PlanRequest
from planner.tasks import build_packages_task


@pytest.mark.django_db
def test_airport_search_endpoint_returns_iata_match():
    Airport.objects.create(
        iata="TBS",
        name="Tbilisi International Airport",
        city="Tbilisi",
        country="GE",
        country_code="GE",
        latitude=41.6692,
        longitude=44.9547,
        timezone="Asia/Tbilisi",
        search_blob="tbs tbilisi georgia",
    )

    client = APIClient()
    response = client.get("/api/airports/search", {"q": "tbs"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["results"]
    assert payload["results"][0]["iata"] == "TBS"


@pytest.mark.django_db
def test_plan_start_validation_and_success_payload_shape(django_capture_on_commit_callbacks):
    user = User.objects.create_user(username="planner", password="safe-pass")
    Airport.objects.create(iata="TBS", name="Tbilisi International Airport", city="Tbilisi", country="GE", country_code="GE", latitude=41.6692, longitude=44.9547, timezone="Asia/Tbilisi", search_blob="tbs tbilisi ge")
    Airport.objects.create(iata="JFK", name="John F Kennedy International Airport", city="New York", country="US", country_code="US", latitude=40.6413, longitude=-73.7781, timezone="America/New_York", search_blob="jfk new york us")

    client = APIClient()
    client.force_authenticate(user=user)

    bad_payload = {
        "origin_iata": "TBS",
        "destination_iata": "ZZZ",
        "search_mode": "direct",
        "departure_date_from": str(timezone.now().date() + timedelta(days=40)),
        "departure_date_to": str(timezone.now().date() + timedelta(days=42)),
        "trip_length_min": 5,
        "trip_length_max": 7,
        "adults": 2,
        "children": 0,
        "search_currency": "USD",
    }
    bad_response = client.post("/api/plans/start", data=bad_payload, format="json")
    assert bad_response.status_code == 400
    assert bad_response.json()["detail"] == "validation_error"

    good_payload = {
        "origin_iata": "TBS",
        "destination_iata": "JFK",
        "search_mode": "direct",
        "departure_date_from": str(timezone.now().date() + timedelta(days=40)),
        "departure_date_to": str(timezone.now().date() + timedelta(days=42)),
        "trip_length_min": 5,
        "trip_length_max": 7,
        "adults": 2,
        "children": 0,
        "search_currency": "USD",
    }

    with patch("planner.tasks.run_plan_pipeline.delay") as mocked_delay:
        with django_capture_on_commit_callbacks(execute=True):
            good_response = client.post("/api/plans/start", data=good_payload, format="json")

    assert good_response.status_code == 202
    payload = good_response.json()
    assert payload["plan_id"]
    assert payload["status_url"].endswith(f"/api/plans/{payload['plan_id']}/status")
    assert payload["results_url"].endswith(f"/plans/{payload['plan_id']}/")
    mocked_delay.assert_called_once()


@pytest.mark.django_db
def test_pipeline_partial_failure_resilience_completes_with_available_candidate():
    user = User.objects.create_user(username="resilient", password="safe-pass")
    depart = timezone.now().date() + timedelta(days=30)
    ret = timezone.now().date() + timedelta(days=36)

    plan = PlanRequest.objects.create(
        user=user,
        origin_input="TBS",
        origin_code="TBS",
        origin_iata="TBS",
        destination_iata="JFK",
        destination_iatas=["JFK", "LHR"],
        search_mode=PlanRequest.SearchMode.DIRECT,
        destination_country="US",
        date_mode=PlanRequest.DateMode.EXACT,
        depart_date=depart,
        return_date=ret,
        departure_date_from=depart,
        departure_date_to=depart,
        trip_length_min=5,
        trip_length_max=7,
        nights_min=5,
        nights_max=7,
        total_budget=Decimal("3000.00"),
        travelers=2,
        adults=2,
        children=0,
        search_currency="USD",
        status=PlanRequest.Status.SCORING,
        explore_constraints={"origin_timezone": "Asia/Tbilisi"},
    )

    good_candidate = DestinationCandidate.objects.create(
        plan=plan,
        country_code="US",
        city_name="New York",
        airport_code="JFK",
        timezone="America/New_York",
        rank=1,
        metadata={"tags": ["culture", "food"], "tier": "premium"},
    )
    broken_candidate = DestinationCandidate.objects.create(
        plan=plan,
        country_code="GB",
        city_name="London",
        airport_code="LHR",
        timezone="Europe/London",
        rank=2,
        metadata={"tags": ["culture"], "tier": "standard"},
    )

    FlightOption.objects.create(
        plan=plan,
        candidate=good_candidate,
        provider="travelpayouts",
        external_offer_id="flight-good",
        origin_airport="TBS",
        destination_airport="JFK",
        stops=1,
        duration_minutes=600,
        currency="USD",
        total_price=Decimal("780.00"),
        deeplink_url="https://www.aviasales.com/offer/flight-good",
        link_type="item",
        raw_payload={"estimated_min": "760.00", "estimated_max": "900.00", "distance_band": "long", "season_multiplier": 1.02, "data_source": "travelpayouts"},
        last_checked_at=timezone.now(),
    )
    HotelOption.objects.create(
        plan=plan,
        candidate=good_candidate,
        provider="travelpayouts",
        external_offer_id="hotel-good",
        provider_property_id="hotel-good-nyc",
        name="NYC Central",
        star_rating=4.1,
        guest_rating=8.4,
        currency="USD",
        total_price=Decimal("1120.00"),
        deeplink_url="https://www.booking.com/hotel/us/nyc-central.html?checkin=2026-07-01&checkout=2026-07-06",
        link_type="item",
        raw_payload={"nightly_min": "180.00", "nightly_max": "260.00", "distance_band": "long", "season_multiplier": 1.02, "data_source": "travelpayouts"},
        last_checked_at=timezone.now(),
    )

    # Candidate 2 intentionally misses hotel data to emulate stage failure/partial data.
    FlightOption.objects.create(
        plan=plan,
        candidate=broken_candidate,
        provider="travelpayouts",
        external_offer_id="flight-broken",
        origin_airport="TBS",
        destination_airport="LHR",
        stops=1,
        duration_minutes=320,
        currency="USD",
        total_price=Decimal("390.00"),
        deeplink_url="https://www.aviasales.com/offer/flight-broken",
        link_type="item",
        raw_payload={"estimated_min": "360.00", "estimated_max": "520.00", "distance_band": "medium", "season_multiplier": 1.0, "data_source": "travelpayouts"},
        last_checked_at=timezone.now(),
    )

    build_packages_task.run(str(plan.id))
    plan.refresh_from_db()

    assert plan.status == PlanRequest.Status.COMPLETED
    assert plan.package_options.count() >= 1
    assert plan.package_options.filter(candidate=good_candidate).exists()
