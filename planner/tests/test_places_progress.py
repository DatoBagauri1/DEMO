from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

import httpx
import pytest
from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import Client
from django.utils import timezone

from planner.models import DestinationCandidate, FlightOption, HotelOption, PlanRequest, ProviderCall, ProviderError
from planner.services.places import PlacesFetchResult, fetch_places_result
from planner.tasks import build_packages_task, fetch_places_for_candidate, places_stage_complete


def _create_plan_with_candidate(user: User) -> tuple[PlanRequest, DestinationCandidate]:
    depart = timezone.now().date() + timedelta(days=30)
    ret = timezone.now().date() + timedelta(days=36)
    plan = PlanRequest.objects.create(
        user=user,
        origin_input="TBS",
        origin_code="TBS",
        origin_iata="TBS",
        destination_input="JFK",
        destination_iata="JFK",
        destination_iatas=["JFK"],
        destination_country="US",
        search_mode=PlanRequest.SearchMode.DIRECT,
        date_mode=PlanRequest.DateMode.EXACT,
        depart_date=depart,
        return_date=ret,
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
        status=PlanRequest.Status.FETCHING_PLACES,
        progress_message="Fetching must-see places...",
        progress_percent=76,
        explore_constraints={"origin_timezone": "Asia/Tbilisi"},
    )
    candidate = DestinationCandidate.objects.create(
        plan=plan,
        country_code="US",
        city_name="New York",
        airport_code="JFK",
        latitude=40.6413,
        longitude=-73.7781,
        timezone="America/New_York",
        rank=1,
        metadata={"tier": "premium", "tags": ["culture", "food"]},
    )
    return plan, candidate


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self._payload = payload

    def json(self) -> dict:
        return self._payload


@pytest.mark.django_db
def test_wikipedia_places_request_uses_user_agent_and_timeout(monkeypatch):
    monkeypatch.setenv("TRIPPILOT_USER_AGENT", "TriPPlanner/1.0 (contact: qa@example.com)")
    cache.clear()
    captured: list[dict] = []

    def fake_get(self, url, params=None):  # noqa: ANN001
        captured.append(
            {
                "user_agent": self.headers.get("User-Agent"),
                "accept": self.headers.get("Accept"),
                "timeout": self.timeout,
                "url": str(url),
            },
        )
        if params and params.get("list") == "geosearch":
            return _FakeResponse(
                200,
                {
                    "query": {
                        "geosearch": [
                            {"pageid": 1, "title": "Eiffel Tower"},
                        ],
                    },
                },
            )
        return _FakeResponse(
            200,
            {
                "query": {
                    "pages": {
                        "1": {
                            "fullurl": "https://en.wikipedia.org/wiki/Eiffel_Tower",
                            "thumbnail": {"source": "https://upload.wikimedia.org/eiffel.jpg"},
                        },
                    },
                },
            },
        )

    monkeypatch.setattr(httpx.Client, "get", fake_get, raising=True)

    result = fetch_places_result(
        city="Paris",
        country="FR",
        latitude=48.8566,
        longitude=2.3522,
        limit=1,
    )

    assert result.places
    assert captured
    first = captured[0]
    assert first["url"] == "https://en.wikipedia.org/w/api.php"
    assert first["user_agent"] == "TriPPlanner/1.0 (contact: qa@example.com)"
    assert "application/json" in str(first["accept"])
    timeout = first["timeout"]
    assert timeout.connect is not None
    assert timeout.read is not None


@pytest.mark.django_db
def test_places_task_returns_not_ok_on_wikipedia_403():
    user = User.objects.create_user(username="places403", password="safe-pass")
    plan, candidate = _create_plan_with_candidate(user)
    fallback = [
        {
            "title": "New York City Center",
            "link": "https://www.google.com/search?q=New+York+City+Center",
            "image_url": "https://images.unsplash.com/fallback",
        },
    ]

    with patch(
        "planner.tasks.fetch_places_result",
        return_value=PlacesFetchResult(
            places=fallback,
            source="fallback",
            partial=True,
            error="HTTP 403 for Wikimedia API",
            http_status=403,
        ),
    ):
        payload = fetch_places_for_candidate.run(str(plan.id), candidate.id)

    assert payload["candidate_id"] == candidate.id
    assert payload["ok"] is False
    assert payload["partial"] is True
    assert "403" in payload["error"]

    candidate.refresh_from_db()
    assert candidate.metadata.get("places_partial") is True
    assert candidate.metadata.get("places_source") == "fallback"
    assert "403" in (candidate.metadata.get("places_error") or "")

    provider_call = ProviderCall.objects.filter(provider="places", plan=plan).latest("created_at")
    assert provider_call.success is False
    assert provider_call.http_status == 403
    provider_error = ProviderError.objects.filter(provider="places", plan=plan).latest("created_at")
    assert "fallback used" in provider_error.error_message


@pytest.mark.django_db
def test_wikipedia_403_handled_gracefully_and_marks_partial_failure():
    user = User.objects.create_user(username="places403_graceful", password="safe-pass")
    plan, candidate = _create_plan_with_candidate(user)

    with patch(
        "planner.tasks.fetch_places_result",
        return_value=PlacesFetchResult(
            places=[
                {
                    "title": "Fallback Place",
                    "link": "https://www.google.com/search?q=Fallback+Place",
                    "image_url": "https://images.unsplash.com/fallback",
                },
            ],
            source="fallback",
            partial=True,
            error="HTTP 403 for Wikimedia API",
            http_status=403,
        ),
    ):
        payload = fetch_places_for_candidate.run(str(plan.id), candidate.id)

    assert payload["ok"] is False
    assert payload["partial"] is True
    assert "403" in payload["error"]

    candidate.refresh_from_db()
    assert candidate.metadata["places_partial"] is True
    assert candidate.metadata["places_source"] == "fallback"
    assert "403" in candidate.metadata["places_error"]


@pytest.mark.django_db
def test_pipeline_continues_after_places_403_and_completes():
    user = User.objects.create_user(username="places_pipeline", password="safe-pass")
    plan, candidate = _create_plan_with_candidate(user)

    FlightOption.objects.create(
        plan=plan,
        candidate=candidate,
        provider="travelpayouts",
        external_offer_id="flight-jfk",
        origin_airport="TBS",
        destination_airport="JFK",
        stops=1,
        duration_minutes=600,
        currency="USD",
        total_price=Decimal("760.00"),
        deeplink_url="https://www.aviasales.com/offer/flight-jfk",
        link_type="item",
        raw_payload={"estimated_min": "700.00", "estimated_max": "820.00", "data_source": "travelpayouts"},
        last_checked_at=timezone.now(),
    )
    HotelOption.objects.create(
        plan=plan,
        candidate=candidate,
        provider="travelpayouts",
        external_offer_id="hotel-jfk",
        provider_property_id="hotel-jfk-1",
        name="NYC partner hotels",
        star_rating=4.0,
        guest_rating=8.2,
        currency="USD",
        total_price=Decimal("980.00"),
        deeplink_url="https://www.booking.com/hotel/us/nyc-partner-hotels.html?checkin=2026-07-01&checkout=2026-07-06",
        link_type="item",
        raw_payload={"nightly_min": "150.00", "nightly_max": "220.00", "data_source": "travelpayouts"},
        last_checked_at=timezone.now(),
    )

    with patch(
        "planner.tasks.fetch_places_result",
        return_value=PlacesFetchResult(
            places=[
                {
                    "title": "New York City Center",
                    "link": "https://www.google.com/search?q=New+York+City+Center",
                    "image_url": "https://images.unsplash.com/fallback",
                },
            ],
            source="fallback",
            partial=True,
            error="HTTP 403 for Wikimedia API",
            http_status=403,
        ),
    ):
        places_payload = fetch_places_for_candidate.run(str(plan.id), candidate.id)

    def run_scoring_inline(plan_id: str):  # noqa: ANN001
        build_packages_task.run(plan_id)
        return None

    with patch("planner.tasks.build_packages_task.delay", side_effect=run_scoring_inline):
        places_stage_complete.run([places_payload], str(plan.id))

    plan.refresh_from_db()
    assert places_payload["ok"] is False
    assert plan.progress_percent == 100
    assert plan.status == PlanRequest.Status.COMPLETED
    assert plan.package_options.count() >= 1


@pytest.mark.django_db
def test_progress_polling_endpoint_returns_fresh_status_and_no_store_header():
    user = User.objects.create_user(username="polling_user", password="safe-pass")
    depart = timezone.now().date() + timedelta(days=28)
    plan = PlanRequest.objects.create(
        user=user,
        origin_input="TBS",
        origin_code="TBS",
        origin_iata="TBS",
        destination_input="JFK",
        destination_iata="JFK",
        destination_iatas=["JFK"],
        destination_country="US",
        search_mode=PlanRequest.SearchMode.DIRECT,
        date_mode=PlanRequest.DateMode.EXACT,
        depart_date=depart,
        return_date=depart + timedelta(days=6),
        departure_date_from=depart,
        departure_date_to=depart,
        trip_length_min=5,
        trip_length_max=7,
        nights_min=5,
        nights_max=7,
        total_budget=Decimal("2400.00"),
        travelers=2,
        adults=2,
        children=0,
        search_currency="USD",
        status=PlanRequest.Status.FETCHING_FLIGHT_SIGNALS,
        progress_message="Fetching flight signals...",
        progress_percent=30,
    )

    client = Client()
    client.force_login(user)

    first = client.get(f"/plans/{plan.id}/progress/", HTTP_HOST="localhost")
    assert first.status_code == 200
    assert "no-store" in first.headers.get("Cache-Control", "")
    assert "Fetching flight signals..." in first.content.decode()
    assert "30%" in first.content.decode()

    PlanRequest.objects.filter(pk=plan.id).update(
        status=PlanRequest.Status.SCORING,
        progress_message="Scoring packages...",
        progress_percent=88,
    )
    second = client.get(f"/plans/{plan.id}/progress/", HTTP_HOST="localhost")
    assert second.status_code == 200
    body = second.content.decode()
    assert "Scoring packages..." in body
    assert "88%" in body
