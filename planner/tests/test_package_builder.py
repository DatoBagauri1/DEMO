from datetime import timedelta
from decimal import Decimal

import pytest
from django.contrib.auth.models import User
from rest_framework.test import APIRequestFactory
from django.utils import timezone

from planner.models import DestinationCandidate, FlightOption, HotelOption, PlanRequest, TourOption
from planner.services.package_builder import build_packages_for_plan
from planner.serializers import PackageOptionSerializer


@pytest.mark.django_db
def test_build_packages_for_plan_creates_entity_payload_fields():
    user = User.objects.create_user(username="alice", password="safe-pass")
    depart = timezone.now().date() + timedelta(days=30)
    ret = timezone.now().date() + timedelta(days=37)

    plan = PlanRequest.objects.create(
        user=user,
        origin_input="JFK",
        origin_code="JFK",
        origin_iata="JFK",
        search_mode=PlanRequest.SearchMode.DIRECT,
        destination_iata="CDG",
        destination_iatas=["CDG"],
        destination_country="FR",
        date_mode=PlanRequest.DateMode.EXACT,
        depart_date=depart,
        return_date=ret,
        departure_date_from=depart,
        departure_date_to=depart,
        trip_length_min=4,
        trip_length_max=7,
        nights_min=4,
        nights_max=7,
        total_budget=Decimal("2400"),
        travelers=2,
        adults=2,
        children=0,
        search_currency="USD",
        preference_weights={"culture": 1.0, "food": 1.0},
        status=PlanRequest.Status.SCORING,
        explore_constraints={"origin_timezone": "America/New_York"},
    )
    paris = DestinationCandidate.objects.create(
        plan=plan,
        country_code="FR",
        city_name="Paris",
        airport_code="CDG",
        timezone="Europe/Paris",
        rank=1,
        metadata={
            "tier": "premium",
            "tags": ["culture", "food"],
            "nonstop_likelihood": 0.7,
            "entities": {
                "flights": [{"title": "JFK to CDG", "link": "https://www.aviasales.com/search?origin=JFK&destination=CDG", "image_url": "https://images.unsplash.com/f1"}],
                "hotels": [{"title": "Paris Central Suites", "link": "https://www.booking.com/searchresults.html?ss=Paris", "image_url": "https://images.unsplash.com/h1"}],
                "tours": [{"title": "Paris Highlights Tour", "link": "https://www.getyourguide.com/s/?q=Paris", "image_url": "https://images.unsplash.com/t1"}],
                "places": [{"title": "Eiffel Tower", "link": "https://en.wikipedia.org/wiki/Eiffel_Tower", "image_url": "https://upload.wikimedia.org/eiffel.jpg"}],
            },
        },
    )

    FlightOption.objects.create(
        plan=plan,
        candidate=paris,
        provider="travelpayouts",
        external_offer_id="f1",
        origin_airport="JFK",
        destination_airport="CDG",
        airline_codes=["AF"],
        stops=0,
        duration_minutes=430,
        cabin_class="economy",
        currency="USD",
        total_price=Decimal("640"),
        deeplink_url="https://www.aviasales.com/search?origin=JFK&destination=CDG",
        raw_payload={
            "estimated_min": "580.00",
            "estimated_max": "760.00",
            "distance_band": "long",
            "nonstop_likelihood": 0.7,
            "season_multiplier": 1.05,
            "data_source": "travelpayouts",
        },
        last_checked_at=timezone.now(),
    )

    HotelOption.objects.create(
        plan=plan,
        candidate=paris,
        provider="travelpayouts",
        external_offer_id="h1",
        name="Paris partner hotels",
        star_rating=4.3,
        guest_rating=8.7,
        currency="USD",
        total_price=Decimal("980"),
        deeplink_url="https://www.booking.com/searchresults.html?ss=Paris%2C+FR",
        raw_payload={
            "nightly_min": "140.00",
            "nightly_max": "260.00",
            "distance_band": "long",
            "season_multiplier": 1.05,
            "data_source": "travelpayouts",
        },
        last_checked_at=timezone.now(),
    )

    packages = build_packages_for_plan(plan, sort_mode="best_value", max_packages=3)
    assert len(packages) == 1
    package = packages[0]

    assert package.rank == 1
    assert package.flight_url.startswith("https://")
    assert package.hotel_url.startswith("https://")
    assert package.tours_url.startswith("https://")
    assert package.estimated_total_min > 0
    assert package.estimated_total_max >= package.estimated_total_min
    assert package.score_breakdown.get("price_value") is not None
    assert package.score_breakdown.get("safety_fallback") is not None
    assert package.freshness_at is not None
    assert package.flight_entities and package.flight_entities[0]["link"].startswith("https://")
    assert package.hotel_entities and package.hotel_entities[0]["link"].startswith("https://")
    assert package.tour_entities and package.tour_entities[0]["link"].startswith("https://")
    assert package.place_entities and package.place_entities[0]["link"].startswith("https://")


@pytest.mark.django_db
def test_build_packages_for_plan_generates_multiple_variants_for_single_destination_with_tours():
    user = User.objects.create_user(username="variant_builder_user", password="safe-pass")
    depart = timezone.now().date() + timedelta(days=31)
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
        total_budget=Decimal("2500.00"),
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
        rank=1,
        metadata={"tags": ["culture", "food"], "entities": {}},
    )
    flight = FlightOption.objects.create(
        plan=plan,
        candidate=candidate,
        provider="travelpayouts",
        external_offer_id="flight-base",
        origin_airport="TBS",
        destination_airport="JFK",
        stops=1,
        duration_minutes=700,
        cabin_class="economy",
        currency="USD",
        total_price=Decimal("750.00"),
        deeplink_url="https://www.aviasales.com/search?origin=TBS&destination=JFK",
        raw_payload={"estimated_min": "700.00", "estimated_max": "820.00", "distance_band": "long"},
        last_checked_at=timezone.now(),
    )
    hotel = HotelOption.objects.create(
        plan=plan,
        candidate=candidate,
        provider="travelpayouts",
        external_offer_id="hotel-base",
        name="NYC hotel search",
        star_rating=4.0,
        guest_rating=8.1,
        currency="USD",
        total_price=Decimal("1100.00"),
        deeplink_url="https://www.booking.com/searchresults.html?ss=New+York",
        raw_payload={"nightly_min": "180.00", "nightly_max": "240.00"},
        last_checked_at=timezone.now(),
    )
    assert flight is not None and hotel is not None

    for idx in range(1, 4):
        TourOption.objects.create(
            plan=plan,
            candidate=candidate,
            provider="travelpayouts",
            external_product_id=f"tour-{idx}",
            name=f"NYC tour {idx}",
            currency="USD",
            total_price=Decimal("25.00") * idx,
            amount_minor=2500 * idx,
            deeplink_url=f"https://www.getyourguide.com/s/?q=NYC+tour+{idx}",
            link_type="search",
            link_confidence=0.45,
            link_rationale="Curated tour search fallback.",
            raw_payload={},
            last_checked_at=timezone.now(),
        )

    packages = build_packages_for_plan(plan, sort_mode="budget_first", max_packages=5)

    assert len(packages) >= 4
    assert len({tuple(pkg.selected_tour_option_ids) for pkg in packages}) >= 3


@pytest.mark.django_db
def test_build_packages_for_plan_dedupes_identical_visible_cards():
    user = User.objects.create_user(username="dedupe_visible_cards_user", password="safe-pass")
    depart = timezone.now().date() + timedelta(days=28)
    ret = depart + timedelta(days=4)

    plan = PlanRequest.objects.create(
        user=user,
        origin_input="JFK",
        origin_code="JFK",
        origin_iata="JFK",
        search_mode=PlanRequest.SearchMode.DIRECT,
        destination_iata="CDG",
        destination_iatas=["CDG"],
        destination_country="FR",
        date_mode=PlanRequest.DateMode.EXACT,
        depart_date=depart,
        return_date=ret,
        departure_date_from=depart,
        departure_date_to=depart,
        trip_length_min=4,
        trip_length_max=4,
        nights_min=4,
        nights_max=4,
        total_budget=Decimal("2500.00"),
        travelers=2,
        adults=2,
        children=0,
        search_currency="USD",
        status=PlanRequest.Status.SCORING,
        explore_constraints={"origin_timezone": "America/New_York"},
    )
    candidate = DestinationCandidate.objects.create(
        plan=plan,
        country_code="FR",
        city_name="Paris",
        airport_code="CDG",
        rank=1,
        metadata={"tags": ["culture"], "entities": {}},
    )

    for idx in range(2):
        FlightOption.objects.create(
            plan=plan,
            candidate=candidate,
            provider="travelpayouts",
            external_offer_id=f"dup-flight-{idx}",
            origin_airport="JFK",
            destination_airport="CDG",
            stops=0,
            duration_minutes=430,
            cabin_class="economy",
            currency="USD",
            total_price=Decimal("640.55"),
            deeplink_url="https://www.aviasales.com/search?origin=JFK&destination=CDG",
            raw_payload={"estimated_min": "620.55", "estimated_max": "700.55"},
            last_checked_at=timezone.now(),
        )
        HotelOption.objects.create(
            plan=plan,
            candidate=candidate,
            provider="travelpayouts",
            external_offer_id=f"dup-hotel-{idx}",
            provider_property_id="search:tp:hotel:paris:dup",
            name="Paris hotel search",
            star_rating=4.0,
            guest_rating=8.4,
            neighborhood="Center",
            currency="USD",
            total_price=Decimal("980.40"),
            deeplink_url="https://www.booking.com/searchresults.html?ss=Paris",
            raw_payload={"nightly_min": "220.10", "nightly_max": "260.10"},
            last_checked_at=timezone.now(),
        )

    packages = build_packages_for_plan(plan, sort_mode="best_value", max_packages=5)

    assert len(packages) == 1
    assert plan.package_options.count() == 1


@pytest.mark.django_db
def test_package_contains_specific_components():
    user = User.objects.create_user(username="pkg_specific", password="safe-pass")
    depart = timezone.now().date() + timedelta(days=35)
    ret = depart + timedelta(days=5)
    plan = PlanRequest.objects.create(
        user=user,
        origin_input="JFK",
        origin_code="JFK",
        origin_iata="JFK",
        search_mode=PlanRequest.SearchMode.DIRECT,
        destination_iata="CDG",
        destination_iatas=["CDG"],
        destination_country="FR",
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
        explore_constraints={"origin_timezone": "America/New_York"},
    )
    candidate = DestinationCandidate.objects.create(
        plan=plan,
        country_code="FR",
        city_name="Paris",
        airport_code="CDG",
        rank=1,
        metadata={"tags": ["culture", "family"], "entities": {}},
    )
    FlightOption.objects.create(
        plan=plan,
        candidate=candidate,
        provider="travelpayouts",
        external_offer_id="flight-specific-1",
        origin_airport="JFK",
        destination_airport="CDG",
        stops=0,
        duration_minutes=430,
        cabin_class="economy",
        currency="USD",
        total_price=Decimal("640.00"),
        deeplink_url="https://www.aviasales.com/search?origin=JFK&destination=CDG&depart_date=2026-06-01",
        link_type="search",
        link_confidence=0.9,
        link_rationale="Parameterized flight search deeplink.",
        raw_payload={"estimated_min": "600.00", "estimated_max": "700.00", "stable_offer_id": "tp:flight:cdg:1"},
        last_checked_at=timezone.now(),
    )
    HotelOption.objects.create(
        plan=plan,
        candidate=candidate,
        provider="travelpayouts",
        external_offer_id="hotel-fallback-1",
        provider_property_id="search:tp:hotel:cdg:1",
        name="Paris hotel search (selected dates)",
        star_rating=4.0,
        guest_rating=8.4,
        neighborhood="City center",
        currency="USD",
        total_price=Decimal("1100.00"),
        deeplink_url="https://www.booking.com/searchresults.html?ss=Paris&checkin=2026-06-01&checkout=2026-06-06",
        link_type="search",
        link_confidence=0.58,
        link_rationale="Search-link fallback.",
        raw_payload={"nightly_min": "180.00", "nightly_max": "220.00", "provider_property_id": "search:tp:hotel:cdg:1"},
        last_checked_at=timezone.now(),
    )

    [package] = build_packages_for_plan(plan, max_packages=1)
    request = APIRequestFactory().get("/api/plans/test/packages")
    payload = PackageOptionSerializer(package, context={"request": request}).data

    assert payload["components"]["flight"]["outbound_url"].startswith("https://")
    assert payload["components"]["hotel"]["outbound_url"].startswith("https://")
    assert payload["components"]["flight"]["stable_id"]
    assert payload["components"]["hotel"]["provider_property_id"]

    for group in ("flights", "hotels", "tours"):
        for entity in payload[group]:
            assert entity["outbound_url"].startswith("https://")
            if entity["link_type"] == "item":
                assert entity["stable_id"]


@pytest.mark.django_db
def test_total_price_breakdown_consistency():
    user = User.objects.create_user(username="pkg_breakdown", password="safe-pass")
    depart = timezone.now().date() + timedelta(days=40)
    ret = depart + timedelta(days=4)
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
        trip_length_min=4,
        trip_length_max=4,
        nights_min=4,
        nights_max=4,
        total_budget=Decimal("2400.00"),
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
        rank=1,
        metadata={"tags": ["culture"], "entities": {}},
    )
    FlightOption.objects.create(
        plan=plan,
        candidate=candidate,
        provider="travelpayouts",
        external_offer_id="f1",
        origin_airport="TBS",
        destination_airport="JFK",
        stops=1,
        duration_minutes=600,
        cabin_class="economy",
        currency="USD",
        total_price=Decimal("800.00"),
        deeplink_url="https://www.aviasales.com/search?origin=TBS&destination=JFK",
        raw_payload={"estimated_min": "760.00", "estimated_max": "890.00"},
        last_checked_at=timezone.now(),
    )
    HotelOption.objects.create(
        plan=plan,
        candidate=candidate,
        provider="travelpayouts",
        external_offer_id="h1",
        provider_property_id="search:tp:jfk:1",
        name="New York hotel search (selected dates)",
        star_rating=4.0,
        guest_rating=8.1,
        neighborhood="Midtown",
        currency="USD",
        total_price=Decimal("960.00"),
        deeplink_url="https://www.booking.com/searchresults.html?ss=New+York",
        raw_payload={"nightly_min": "190.00", "nightly_max": "260.00"},
        last_checked_at=timezone.now(),
    )

    [package] = build_packages_for_plan(plan, max_packages=1)
    breakdown = package.price_breakdown
    flight_total = Decimal(str(breakdown["flight_total"]))
    hotel_total = Decimal(str(breakdown["hotel_total"]))
    tours_total = Decimal(str(breakdown["tours_total"]))
    package_total = Decimal(str(breakdown["package_total"]))

    assert abs(package_total - (flight_total + hotel_total + tours_total)) <= Decimal("0.01")
    assert abs(Decimal(str(package.total_price)) - package_total) <= Decimal("0.01")
