from __future__ import annotations

import re
from datetime import timedelta
from decimal import Decimal
from html import unescape

import pytest
from django.contrib.auth.models import User
from django.utils import timezone

from planner.models import DestinationCandidate, FlightOption, HotelOption, PackageOption, PlanRequest, SavedPackage


def _build_completed_plan(user: User, package_count: int = 1, *, use_package_urls: bool = True) -> tuple[PlanRequest, list[PackageOption]]:
    depart = timezone.now().date() + timedelta(days=32)
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
        return_date=depart + timedelta(days=6),
        departure_date_from=depart,
        departure_date_to=depart,
        trip_length_min=5,
        trip_length_max=7,
        nights_min=5,
        nights_max=7,
        total_budget=Decimal("2600.00"),
        travelers=2,
        adults=2,
        children=0,
        search_currency="USD",
        status=PlanRequest.Status.COMPLETED,
        progress_percent=100,
        progress_message="Completed",
    )

    packages: list[PackageOption] = []
    airports = ["JFK", "LHR", "CDG", "FCO", "DXB"]
    cities = ["New York", "London", "Paris", "Rome", "Dubai"]
    plan_marker = str(plan.id)[:8]
    for idx in range(package_count):
        airport_code = airports[idx % len(airports)]
        city_name = cities[idx % len(cities)]
        candidate = DestinationCandidate.objects.create(
            plan=plan,
            country_code="US" if airport_code == "JFK" else "GB",
            city_name=city_name,
            airport_code=airport_code,
            rank=idx + 1,
            metadata={"tags": ["culture"]},
        )
        flight_link = f"https://www.aviasales.com/search?origin=TBS&destination={airport_code}&offer={idx}&plan={plan_marker}"
        hotel_link = f"https://www.booking.com/searchresults.html?ss={city_name.replace(' ', '+')}&offer={idx}&plan={plan_marker}"
        flight = FlightOption.objects.create(
            plan=plan,
            candidate=candidate,
            provider="travelpayouts",
            external_offer_id=f"flight-{idx}",
            origin_airport="TBS",
            destination_airport=airport_code,
            stops=1,
            duration_minutes=420 + (idx * 15),
            cabin_class="economy",
            currency="USD",
            total_price=Decimal("740.00") + Decimal(str(idx)),
            deeplink_url=flight_link,
            raw_payload={},
            last_checked_at=timezone.now(),
        )
        hotel = HotelOption.objects.create(
            plan=plan,
            candidate=candidate,
            provider="travelpayouts",
            external_offer_id=f"hotel-{idx}",
            name=f"{city_name} Central",
            star_rating=4.1,
            guest_rating=8.3,
            currency="USD",
            total_price=Decimal("1120.00") + Decimal(str(idx)),
            deeplink_url=hotel_link,
            raw_payload={},
            last_checked_at=timezone.now(),
        )
        package = PackageOption.objects.create(
            plan=plan,
            candidate=candidate,
            flight_option=flight,
            hotel_option=hotel,
            rank=idx + 1,
            currency="USD",
            total_price=Decimal("1860.00") + Decimal(str(idx)),
            estimated_total_min=Decimal("1750.00") + Decimal(str(idx)),
            estimated_total_max=Decimal("2050.00") + Decimal(str(idx)),
            estimated_flight_min=Decimal("700.00"),
            estimated_flight_max=Decimal("850.00"),
            estimated_hotel_nightly_min=Decimal("160.00"),
            estimated_hotel_nightly_max=Decimal("230.00"),
            freshness_at=timezone.now(),
            flight_url=flight_link if use_package_urls else "",
            hotel_url=hotel_link if use_package_urls else "",
            tours_url=f"https://www.getyourguide.com/s/?q={city_name.replace(' ', '+')}",
            flight_entities=[],
            hotel_entities=[],
            tour_entities=[],
            place_entities=[],
            data_confidence=0.9,
            score=89.0 - idx,
            price_score=88.0 - idx,
            convenience_score=80.0 - idx,
            quality_score=82.0 - idx,
            location_score=77.0 - idx,
            explanations=["Balanced value"],
            score_breakdown={"explanations": ["Balanced value"]},
            last_scored_at=timezone.now(),
        )
        packages.append(package)

    plan.progress_message = f"Found {package_count} ranked links-only packages."
    plan.save(update_fields=["progress_message", "updated_at"])
    return plan, packages


@pytest.mark.django_db
def test_plan_results_render_non_empty_when_packages_exist(client):
    user = User.objects.create_user(username="render_visible_user", password="safe-pass")
    plan, packages = _build_completed_plan(user, package_count=1)

    client.force_login(user)
    results_response = client.get(f"/plans/{plan.id}/", HTTP_HOST="localhost")
    assert results_response.status_code == 200
    results_html = unescape(results_response.content.decode())
    assert "Share link" not in results_html

    cards_response = client.get(f"/plans/{plan.id}/packages/?sort=best_value", HTTP_HOST="localhost")
    assert cards_response.status_code == 200
    html = unescape(cards_response.content.decode())

    assert "tp-package-card" in html
    assert "View Flight" in html
    assert "toggle-saved-place" not in html
    assert packages[0].flight_url in html


@pytest.mark.django_db
def test_packages_render_when_found_gt_zero(client):
    user = User.objects.create_user(username="render_found_gt_zero", password="safe-pass")
    plan, packages = _build_completed_plan(user, package_count=2)
    plan.progress_message = "Found 2 ranked links-only packages."
    plan.progress_percent = 100
    plan.save(update_fields=["progress_message", "progress_percent", "updated_at"])

    client.force_login(user)
    progress_response = client.get(f"/plans/{plan.id}/progress/", HTTP_HOST="localhost")
    cards_response = client.get(f"/plans/{plan.id}/packages/?sort=best_value", HTTP_HOST="localhost")

    assert progress_response.status_code == 200
    assert cards_response.status_code == 200

    progress_html = unescape(progress_response.content.decode())
    cards_html = unescape(cards_response.content.decode())

    assert "Found 1 ranked links-only package." in progress_html
    assert cards_html.count("tp-package-card") == 1
    assert "View Flight" in cards_html
    assert "View Hotel" in cards_html
    assert "View Tours" in cards_html
    assert packages[0].hotel_url in cards_html


@pytest.mark.django_db
def test_results_query_uses_correct_fk_and_field_mapping(client):
    user = User.objects.create_user(username="mapping_user", password="safe-pass")
    main_plan, main_packages = _build_completed_plan(user, package_count=1, use_package_urls=False)
    other_plan, _ = _build_completed_plan(user, package_count=1)

    client.force_login(user)
    response = client.get(f"/plans/{main_plan.id}/packages/?sort=best_value", HTTP_HOST="localhost")
    assert response.status_code == 200
    html = unescape(response.content.decode())

    main_package = main_packages[0]
    assert main_package.flight_option.deeplink_url in html
    assert main_package.hotel_option.deeplink_url in html

    other_package = other_plan.package_options.select_related("flight_option").first()
    assert other_package is not None
    assert other_package.flight_option.deeplink_url not in html


@pytest.mark.django_db
def test_summary_count_matches_rendered_list(client):
    user = User.objects.create_user(username="count_match_user", password="safe-pass")
    plan, _ = _build_completed_plan(user, package_count=2)

    client.force_login(user)
    progress_response = client.get(f"/plans/{plan.id}/progress/", HTTP_HOST="localhost")
    assert progress_response.status_code == 200
    progress_html = unescape(progress_response.content.decode())

    match = re.search(r"Found (\d+) ranked links-only package(?:s)?\.", progress_html)
    assert match is not None
    summary_count = int(match.group(1))

    cards_response = client.get(f"/plans/{plan.id}/packages/?sort=best_value", HTTP_HOST="localhost")
    assert cards_response.status_code == 200
    cards_html = unescape(cards_response.content.decode())
    rendered_count = cards_html.count("tp-package-card")

    assert rendered_count == summary_count == 1


@pytest.mark.django_db
def test_package_cards_partial_hides_duplicate_visible_packages(client):
    user = User.objects.create_user(username="render_dedupe_user", password="safe-pass")
    plan, packages = _build_completed_plan(user, package_count=1)
    package = packages[0]

    PackageOption.objects.create(
        plan=plan,
        candidate=package.candidate,
        flight_option=package.flight_option,
        hotel_option=package.hotel_option,
        rank=2,
        currency=package.currency,
        total_price=package.total_price,
        estimated_total_min=package.estimated_total_min,
        estimated_total_max=package.estimated_total_max,
        estimated_flight_min=package.estimated_flight_min,
        estimated_flight_max=package.estimated_flight_max,
        estimated_hotel_nightly_min=package.estimated_hotel_nightly_min,
        estimated_hotel_nightly_max=package.estimated_hotel_nightly_max,
        freshness_at=package.freshness_at,
        flight_url=package.flight_url,
        hotel_url=package.hotel_url,
        tours_url=package.tours_url,
        flight_entities=package.flight_entities,
        hotel_entities=package.hotel_entities,
        tour_entities=package.tour_entities,
        place_entities=package.place_entities,
        data_confidence=package.data_confidence,
        score=package.score,
        price_score=package.price_score,
        convenience_score=package.convenience_score,
        quality_score=package.quality_score,
        location_score=package.location_score,
        explanations=package.explanations,
        score_breakdown=package.score_breakdown,
        last_scored_at=package.last_scored_at,
    )

    client.force_login(user)
    cards_response = client.get(f"/plans/{plan.id}/packages/?sort=best_value", HTTP_HOST="localhost")
    progress_response = client.get(f"/plans/{plan.id}/progress/", HTTP_HOST="localhost")

    assert cards_response.status_code == 200
    assert progress_response.status_code == 200

    cards_html = unescape(cards_response.content.decode())
    progress_html = unescape(progress_response.content.decode())

    assert cards_html.count("tp-package-card") == 1
    assert "Found 1 ranked links-only package." in progress_html


@pytest.mark.django_db
def test_package_cards_partial_does_not_render_cabin_class_controls(client):
    user = User.objects.create_user(username="render_no_cabin_label_user", password="safe-pass")
    plan, packages = _build_completed_plan(user, package_count=1)
    package = packages[0]
    assert package is not None

    client.force_login(user)
    cards_response = client.get(f"/plans/{plan.id}/packages/?sort=best_value", HTTP_HOST="localhost")
    assert cards_response.status_code == 200
    cards_html = unescape(cards_response.content.decode()).lower()

    assert "cabin" not in cards_html
    assert "economy" not in cards_html
    assert "business" not in cards_html


@pytest.mark.django_db
def test_package_detail_page_renders_component_links(client):
    user = User.objects.create_user(username="detail_page_user", password="safe-pass")
    plan, packages = _build_completed_plan(user, package_count=1)
    package = packages[0]

    client.force_login(user)
    response = client.get(f"/p/{plan.public_token}/pkg/{package.id}/", HTTP_HOST="localhost")
    assert response.status_code == 200
    html = unescape(response.content.decode())
    assert "Price Breakdown" in html
    assert "View Flight" in html
    assert "View Hotel" in html


@pytest.mark.django_db
def test_package_save_toggle_creates_and_removes_savedpackage(client):
    user = User.objects.create_user(username="save_toggle_user", password="safe-pass")
    plan, packages = _build_completed_plan(user, package_count=1)
    package = packages[0]

    client.force_login(user)

    save_response = client.post(f"/packages/{package.id}/toggle-save/", HTTP_HOST="localhost")
    assert save_response.status_code == 302
    assert SavedPackage.objects.filter(user=user, package=package).exists() is True

    unsave_response = client.post(f"/packages/{package.id}/toggle-save/", HTTP_HOST="localhost")
    assert unsave_response.status_code == 302
    assert SavedPackage.objects.filter(user=user, package=package).exists() is False


@pytest.mark.django_db
def test_package_save_toggle_hx_request_returns_updated_control(client):
    user = User.objects.create_user(username="save_toggle_hx_user", password="safe-pass")
    _, packages = _build_completed_plan(user, package_count=1)
    package = packages[0]

    client.force_login(user)

    response = client.post(
        f"/packages/{package.id}/toggle-save/",
        HTTP_HOST="localhost",
        **{"HTTP_HX_REQUEST": "true"},
    )
    assert response.status_code == 200
    html = unescape(response.content.decode())
    assert "<form" in html
    assert "Saved" in html
    assert SavedPackage.objects.filter(user=user, package=package).exists() is True
