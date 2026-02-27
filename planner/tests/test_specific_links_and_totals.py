from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from django.contrib.auth.models import User
from django.utils import timezone
from rest_framework.test import APIClient

from planner.models import Airport, DestinationCandidate, FlightOption, HotelOption, PlanRequest, TourOption
from planner.serializers import PackageOptionSerializer, PlanStartSerializer
from planner.services.package_builder import build_packages_for_plan


def _seed_airports() -> None:
    Airport.objects.update_or_create(
        iata="TBS",
        defaults={
            "name": "Tbilisi International Airport",
            "city": "Tbilisi",
            "country": "Georgia",
            "country_code": "GE",
            "latitude": 41.6692,
            "longitude": 44.9547,
            "timezone": "Asia/Tbilisi",
            "search_blob": "tbs tbilisi georgia",
        },
    )
    Airport.objects.update_or_create(
        iata="JFK",
        defaults={
            "name": "John F Kennedy International Airport",
            "city": "New York",
            "country": "United States",
            "country_code": "US",
            "latitude": 40.6413,
            "longitude": -73.7781,
            "timezone": "America/New_York",
            "search_blob": "jfk new york usa",
        },
    )


def _plan_and_candidate(user: User) -> tuple[PlanRequest, DestinationCandidate]:
    depart = timezone.now().date() + timedelta(days=33)
    ret = depart + timedelta(days=5)
    plan = PlanRequest.objects.create(
        user=user,
        origin_input="TBS",
        origin_code="TBS",
        origin_iata="TBS",
        search_mode=PlanRequest.SearchMode.DIRECT,
        destination_iata="JFK",
        destination_iatas=["JFK"],
        destination_country="US",
        date_mode=PlanRequest.DateMode.EXACT,
        depart_date=depart,
        return_date=ret,
        departure_date_from=depart,
        departure_date_to=depart,
        trip_length_min=5,
        trip_length_max=5,
        nights_min=5,
        nights_max=5,
        total_budget=Decimal("2800.00"),
        travelers=2,
        adults=2,
        children=0,
        search_currency="USD",
        status=PlanRequest.Status.SCORING,
        explore_constraints={"origin_timezone": "Asia/Tbilisi"},
    )
    candidate = DestinationCandidate.objects.create(
        plan=plan,
        country_code="US",
        city_name="New York",
        airport_code="JFK",
        timezone="America/New_York",
        rank=1,
        metadata={"tags": ["culture", "food"], "entities": {}},
    )
    return plan, candidate


def _attach_options(
    *,
    plan: PlanRequest,
    candidate: DestinationCandidate,
    flight_link_type: str,
    hotel_link_type: str,
    tour_link_type: str | None = None,
) -> None:
    flight_url = "https://www.aviasales.com/search?origin=TBS&destination=JFK"
    if flight_link_type == "item":
        flight_url = "https://www.aviasales.com/offer/offer-abc123"
    hotel_url = "https://www.booking.com/searchresults.html?ss=New+York&checkin=2026-07-01&checkout=2026-07-06"
    if hotel_link_type == "item":
        hotel_url = "https://www.booking.com/hotel/us/example-central.html?checkin=2026-07-01&checkout=2026-07-06"

    FlightOption.objects.create(
        plan=plan,
        candidate=candidate,
        provider="travelpayouts",
        external_offer_id="offer-abc123" if flight_link_type == "item" else "search-flight-1",
        origin_airport="TBS",
        destination_airport="JFK",
        airline_codes=["B6"],
        stops=1,
        duration_minutes=650,
        currency="USD",
        total_price=Decimal("820.00"),
        deeplink_url=flight_url,
        link_type=flight_link_type,
        raw_payload={
            "stable_offer_id": "offer-abc123",
            "estimated_min": "780.00",
            "estimated_max": "900.00",
            "fallback_search": flight_link_type != "item",
        },
        last_checked_at=timezone.now(),
    )
    HotelOption.objects.create(
        plan=plan,
        candidate=candidate,
        provider="travelpayouts",
        external_offer_id="hotel-offer-1" if hotel_link_type == "item" else "search-hotel-1",
        provider_property_id="hotel-prop-001" if hotel_link_type == "item" else "search:hotel-prop-001",
        name="New York Central Hotel",
        star_rating=4.2,
        guest_rating=8.4,
        neighborhood="Midtown",
        currency="USD",
        total_price=Decimal("1100.00"),
        deeplink_url=hotel_url,
        link_type=hotel_link_type,
        raw_payload={
            "nightly_price": "220.00",
            "total_stay_price": "1100.00",
            "provider_property_id": "hotel-prop-001",
            "fallback_search": hotel_link_type != "item",
        },
        last_checked_at=timezone.now(),
    )

    if tour_link_type:
        tour_url = "https://www.getyourguide.com/s/?q=New+York+Walking+Tour"
        if tour_link_type == "item":
            tour_url = "https://www.getyourguide.com/new-york-l59/walking-tour-p12345/"
        TourOption.objects.create(
            plan=plan,
            candidate=candidate,
            provider="travelpayouts",
            external_product_id="tour-product-1",
            name="New York Walking Tour",
            currency="USD",
            total_price=Decimal("95.00"),
            amount_minor=9500,
            deeplink_url=tour_url,
            link_type=tour_link_type,
            raw_payload={"fallback_search": tour_link_type != "item"},
            last_checked_at=timezone.now(),
        )


@pytest.mark.django_db
def test_package_total_equals_sum_of_components():
    user = User.objects.create_user(username="pkg_total_components", password="safe-pass")
    plan, candidate = _plan_and_candidate(user)
    _attach_options(plan=plan, candidate=candidate, flight_link_type="item", hotel_link_type="item")

    [package] = build_packages_for_plan(plan, max_packages=1)
    breakdown = package.price_breakdown

    flight_total = Decimal(str(breakdown["flight"]["amount"]))
    hotel_total = Decimal(str(breakdown["hotel"]["amount"]))
    tours_total = Decimal(str(breakdown["tours"]["amount"]))
    total = Decimal(str(breakdown["total"]["amount"]))

    assert total == flight_total + hotel_total + tours_total
    assert Decimal(str(package.total_price)) == total
    assert Decimal(str(breakdown["package_total"])) == total


@pytest.mark.django_db
def test_package_contains_specific_item_links_when_ids_available():
    user = User.objects.create_user(username="pkg_item_links", password="safe-pass")
    plan, candidate = _plan_and_candidate(user)
    _attach_options(
        plan=plan,
        candidate=candidate,
        flight_link_type="item",
        hotel_link_type="item",
        tour_link_type="item",
    )

    packages = build_packages_for_plan(plan, sort_mode="best_value", max_packages=4)
    assert packages

    first = packages[0]
    assert first.component_summary["flight"]["link_type"] == "item"
    assert first.component_summary["flight"]["fallback_search"] is False
    assert first.component_summary["flight"]["deeplink_url"]
    assert first.component_summary["hotel"]["link_type"] == "item"
    assert first.component_summary["hotel"]["fallback_search"] is False
    assert first.component_summary["hotel"]["deeplink_url"]

    package_with_tour = next((pkg for pkg in packages if pkg.selected_tour_option_ids), None)
    assert package_with_tour is not None
    tours = package_with_tour.component_summary.get("tours") or []
    assert tours
    assert tours[0]["link_type"] == "item"
    assert tours[0]["fallback_search"] is False
    assert tours[0]["deeplink_url"]


@pytest.mark.django_db
def test_no_search_urls_in_primary_results(client):
    user = User.objects.create_user(username="pkg_no_search_primary", password="safe-pass")
    plan, candidate = _plan_and_candidate(user)
    _attach_options(
        plan=plan,
        candidate=candidate,
        flight_link_type="item",
        hotel_link_type="item",
        tour_link_type="search",
    )

    [package] = build_packages_for_plan(plan, max_packages=1)
    plan.status = PlanRequest.Status.COMPLETED
    plan.progress_percent = 100
    plan.progress_message = "Completed"
    plan.save(update_fields=["status", "progress_percent", "progress_message", "updated_at"])

    payload = PackageOptionSerializer(package).data

    assert "searchresults" not in str(payload["components"]["flight"]["deeplink_url"]).lower()
    assert "/search" not in str(payload["components"]["flight"]["deeplink_url"]).lower()
    assert "searchresults" not in str(payload["components"]["hotel"]["deeplink_url"]).lower()
    assert "/search" not in str(payload["components"]["hotel"]["deeplink_url"]).lower()

    client.force_login(user)
    html = client.get(f"/plans/{plan.id}/packages/?sort=best_value", HTTP_HOST="localhost").content.decode().lower()
    assert "view flight" in html
    assert "view hotel" in html
    assert "searchresults.html" not in html


@pytest.mark.django_db
def test_total_excludes_tours():
    user = User.objects.create_user(username="pkg_total_excludes_tours", password="safe-pass")
    plan, candidate = _plan_and_candidate(user)
    _attach_options(
        plan=plan,
        candidate=candidate,
        flight_link_type="item",
        hotel_link_type="item",
        tour_link_type="item",
    )
    packages = build_packages_for_plan(plan, max_packages=2)
    package = next((p for p in packages if p.selected_tour_option_ids), None)
    assert package is not None
    breakdown = package.price_breakdown
    flight_total = Decimal(str(breakdown["flight"]["amount"]))
    hotel_total = Decimal(str(breakdown["hotel"]["amount"]))
    total = Decimal(str(breakdown["total"]["amount"]))
    optional_tours_total = Decimal(str((breakdown.get("optional_tours") or {}).get("amount") or "0.00"))

    assert optional_tours_total > Decimal("0.00")
    assert total == (flight_total + hotel_total)
    assert Decimal(str(package.total_price)) == total


@pytest.mark.django_db
def test_cabin_class_removed(client):
    _seed_airports()
    user = User.objects.create_user(username="no_cabin_user", password="safe-pass")

    depart = timezone.now().date() + timedelta(days=40)
    serializer = PlanStartSerializer(
        data={
            "origin_iata": "TBS",
            "destination_iata": "JFK",
            "search_mode": "direct",
            "departure_date_from": str(depart),
            "departure_date_to": str(depart + timedelta(days=2)),
            "trip_length_min": 4,
            "trip_length_max": 6,
            "adults": 2,
            "children": 0,
            "search_currency": "USD",
            "flight_filters": {"cabin": "business", "max_stops": 1},
        },
    )
    assert serializer.is_valid(), serializer.errors
    assert serializer.validated_data["flight_filters"] == {"max_stops": 1}

    client.force_login(user)
    wizard_html = client.get("/planner/", HTTP_HOST="localhost").content.decode().lower()
    assert "id_cabin" not in wizard_html
    assert "economy" not in wizard_html
    assert "business" not in wizard_html

    api_client = APIClient()
    api_client.force_authenticate(user=user)
    interpret = api_client.post(
        "/api/plans/interpret",
        data={"text": "From TBS to JFK next month in business class for 2 adults"},
        format="json",
    )
    assert interpret.status_code == 200
    fields = interpret.json().get("fields") or {}
    assert "cabin" not in (fields.get("flight_filters") or {})


@pytest.mark.django_db
def test_budget_removed():
    _seed_airports()
    depart = timezone.now().date() + timedelta(days=40)
    serializer = PlanStartSerializer(
        data={
            "origin_iata": "TBS",
            "destination_iata": "JFK",
            "search_mode": "direct",
            "departure_date_from": str(depart),
            "departure_date_to": str(depart + timedelta(days=2)),
            "trip_length_min": 4,
            "trip_length_max": 6,
            "adults": 2,
            "children": 0,
            "search_currency": "USD",
            "total_budget": "2000.00",
        },
    )
    assert serializer.is_valid() is False
    assert "total_budget" in serializer.errors


@pytest.mark.django_db
def test_packages_endpoint_returns_concrete_components():
    user = User.objects.create_user(username="packages_endpoint_components", password="safe-pass")
    plan, candidate = _plan_and_candidate(user)
    _attach_options(
        plan=plan,
        candidate=candidate,
        flight_link_type="item",
        hotel_link_type="item",
        tour_link_type="search",
    )
    build_packages_for_plan(plan, max_packages=1)
    plan.status = PlanRequest.Status.COMPLETED
    plan.progress_percent = 100
    plan.progress_message = "Completed"
    plan.save(update_fields=["status", "progress_percent", "progress_message", "updated_at"])

    client = APIClient()
    client.force_authenticate(user=user)
    response = client.get(f"/api/plans/{plan.id}/packages")

    assert response.status_code == 200
    payload = response.json()
    assert payload
    package = payload[0]

    assert package["flight"]["deeplink_url"]
    assert package["flight"]["link_type"] == "item"
    assert package["hotel"]["deeplink_url"]
    assert package["hotel"]["link_type"] == "item"
    assert isinstance(package["tours"], list)
    assert package["package_total"]
    assert package["price_breakdown"]["total"]["amount"]
    assert package["components"]["flight"]["stable_id"]
    assert package["components"]["hotel"]["stable_id"]
