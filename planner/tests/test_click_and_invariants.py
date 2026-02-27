from datetime import timedelta
from decimal import Decimal

import pytest
from django.contrib.auth.models import User
from django.urls import resolve
from django.utils import timezone
from rest_framework.test import APIClient, APIRequestFactory

from planner import api_urls
from planner.models import ClickEvent, DestinationCandidate, FlightOption, HotelOption, PackageOption, PlanRequest
from planner.serializers import PackageOptionSerializer
from planner.services.security import is_allowed_outbound_url


def _build_plan_with_package(user: User) -> tuple[PlanRequest, PackageOption]:
    depart = timezone.now().date() + timedelta(days=21)
    ret = timezone.now().date() + timedelta(days=27)
    plan = PlanRequest.objects.create(
        user=user,
        origin_input="JFK",
        origin_code="JFK",
        origin_iata="JFK",
        destination_iata="CDG",
        destination_iatas=["CDG"],
        destination_country="FR",
        date_mode=PlanRequest.DateMode.EXACT,
        depart_date=depart,
        return_date=ret,
        departure_date_from=depart,
        departure_date_to=depart,
        trip_length_min=6,
        trip_length_max=6,
        nights_min=4,
        nights_max=6,
        total_budget=Decimal("2100"),
        travelers=2,
        adults=2,
        children=0,
        search_currency="USD",
        status=PlanRequest.Status.COMPLETED,
    )
    candidate = DestinationCandidate.objects.create(
        plan=plan,
        country_code="FR",
        city_name="Paris",
        airport_code="CDG",
        rank=1,
        metadata={"tier": "premium", "tags": ["culture", "food"]},
    )
    flight = FlightOption.objects.create(
        plan=plan,
        candidate=candidate,
        provider="travelpayouts",
        external_offer_id="f-test",
        origin_airport="JFK",
        destination_airport="CDG",
        stops=0,
        duration_minutes=450,
        cabin_class="economy",
        currency="USD",
        total_price=Decimal("640"),
        deeplink_url="https://www.aviasales.com/search?origin=JFK&destination=CDG",
    )
    hotel = HotelOption.objects.create(
        plan=plan,
        candidate=candidate,
        provider="travelpayouts",
        external_offer_id="h-test",
        name="Paris hotels",
        star_rating=4.2,
        guest_rating=8.5,
        currency="USD",
        total_price=Decimal("920"),
        deeplink_url="https://www.booking.com/searchresults.html?ss=Paris",
    )
    package = PackageOption.objects.create(
        plan=plan,
        candidate=candidate,
        flight_option=flight,
        hotel_option=hotel,
        rank=1,
        currency="USD",
        total_price=Decimal("1660"),
        amount_minor=166000,
        estimated_total_min=Decimal("1450"),
        estimated_total_max=Decimal("1890"),
        estimated_flight_min=Decimal("590"),
        estimated_flight_max=Decimal("760"),
        estimated_hotel_nightly_min=Decimal("145"),
        estimated_hotel_nightly_max=Decimal("255"),
        flight_url="https://www.aviasales.com/search?origin=JFK&destination=CDG",
        hotel_url="https://www.booking.com/searchresults.html?ss=Paris",
        tours_url="https://www.getyourguide.com/s/?q=Paris",
        flight_entities=[
            {
                "title": "JFK to CDG",
                "price": "610.00",
                "currency": "USD",
                "link": "https://www.aviasales.com/search?origin=JFK&destination=CDG",
                "image_url": "https://images.unsplash.com/test-flight",
            },
        ],
        hotel_entities=[
            {
                "title": "Paris Central Suites",
                "price": "190.00",
                "currency": "USD",
                "link": "https://www.booking.com/searchresults.html?ss=Paris",
                "image_url": "https://images.unsplash.com/test-hotel",
            },
        ],
        tour_entities=[
            {
                "title": "Paris Highlights Tour",
                "link": "https://www.getyourguide.com/s/?q=Paris+tour",
                "image_url": "https://images.unsplash.com/test-tour",
            },
        ],
        place_entities=[
            {
                "title": "Eiffel Tower",
                "link": "https://en.wikipedia.org/wiki/Eiffel_Tower",
                "image_url": "https://upload.wikimedia.org/eiffel.jpg",
            },
        ],
        score=83.2,
        score_breakdown={"price_value": 80, "convenience": 73, "preference_match": 88, "seasonal_fit": 82, "safety_fallback": 75, "freshness": 95},
    )
    return plan, package


@pytest.mark.django_db
def test_click_endpoint_stores_entity_click_fields():
    user = User.objects.create_user(username="tracker", password="safe-pass")
    plan, package = _build_plan_with_package(user)

    client = APIClient()
    client.force_authenticate(user=user)

    payload = {
        "provider": "places",
        "link_type": "place",
        "destination": "Paris-FR",
        "correlation_id": f"{plan.id}:{package.id}:place",
        "plan_id": str(plan.id),
        "package_id": str(package.id),
        "outbound_url": "https://en.wikipedia.org/wiki/Eiffel_Tower",
    }
    response = client.post("/api/click", data=payload, format="json")

    assert response.status_code == 201
    event = ClickEvent.objects.latest("created_at")
    assert event.plan_id == plan.id
    assert event.package_id == package.id
    assert event.link_type == "place"
    assert event.destination == "Paris-FR"
    assert event.outbound_url.startswith("https://")


@pytest.mark.django_db
def test_links_only_invariant_and_package_entity_payload_shape():
    routes = [str(pattern.pattern) for pattern in api_urls.urlpatterns]
    assert all("checkout" not in route for route in routes)
    assert all("reservation" not in route for route in routes)

    user = User.objects.create_user(username="invariant", password="safe-pass")
    _, package = _build_plan_with_package(user)

    request = APIRequestFactory().get("/api/plans/test/packages")
    payload = PackageOptionSerializer(package, context={"request": request}).data

    assert payload["deeplinks"]["flight_url"].startswith("https://")
    assert payload["deeplinks"]["hotel_url"].startswith("https://")
    assert isinstance(payload["flights"], list)
    assert isinstance(payload["hotels"], list)
    assert isinstance(payload["tours"], list)
    assert isinstance(payload["places"], list)
    assert payload["flights"][0]["link"].startswith("https://")
    assert payload["places"][0]["image_url"]
    forbidden_keys = {"reservation_id", "booking_id", "checkout_url", "payment_url"}
    assert forbidden_keys.isdisjoint(payload.keys())

    match = resolve("/api/click")
    assert match.view_name == "planner-api:click-track"


@pytest.mark.django_db
def test_outbound_link_validation_allows_expected_domains(monkeypatch):
    monkeypatch.setenv("OUTBOUND_URL_ALLOWED_DOMAINS", "booking.com,aviasales.com,getyourguide.com")

    assert is_allowed_outbound_url("https://www.booking.com/searchresults.html?ss=Paris")
    assert is_allowed_outbound_url("https://subdomain.aviasales.com/search?origin=JFK&destination=CDG")
    assert is_allowed_outbound_url("https://www.getyourguide.com/s/?q=Paris")

    assert not is_allowed_outbound_url("javascript:alert(1)")
    assert not is_allowed_outbound_url("https://evil-example.com/redirect?to=booking.com")
