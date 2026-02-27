from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.contrib.auth.models import User
from django.db import transaction
from django.utils import timezone

from planner.models import Airport, PlanRequest
from planner.services.plan_service import create_plan_request


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


def _wizard_payload(*, idempotency_key: str) -> dict[str, str]:
    base = timezone.now().date() + timedelta(days=30)
    return {
        "idempotency_key": idempotency_key,
        "search_mode": "direct",
        "origin_iata": "TBS",
        "destination_iata": "JFK",
        "destination_iatas_text": "JFK",
        "destination_country": "US",
        "departure_date_from": str(base),
        "departure_date_to": str(base + timedelta(days=2)),
        "trip_length_min": "4",
        "trip_length_max": "7",
        "total_budget": "2200.00",
        "adults": "2",
        "children": "0",
        "currency": "USD",
        "hotel_stars_min": "3",
        "hotel_guest_rating_min": "7.5",
        "flight_max_stops": "1",
        "flight_max_duration_minutes": "1200",
    }


def _service_payload() -> dict:
    base = timezone.now().date() + timedelta(days=35)
    return {
        "origin_iata": "TBS",
        "origin_input": "TBS",
        "search_mode": PlanRequest.SearchMode.DIRECT,
        "destination_iata": "JFK",
        "destination_iatas": ["JFK"],
        "destination_input": "JFK",
        "destination_country": "US",
        "departure_date_from": base,
        "departure_date_to": base + timedelta(days=1),
        "trip_length_min": 4,
        "trip_length_max": 7,
        "total_budget": Decimal("2200.00"),
        "adults": 2,
        "children": 0,
        "search_currency": "USD",
        "hotel_filters": {},
        "flight_filters": {},
        "preferences": {},
        "explore_constraints": {},
    }


@pytest.mark.django_db
def test_wizard_post_happy_path_creates_plan(client, django_capture_on_commit_callbacks):
    _seed_airports()
    user = User.objects.create_user(username="wizard_happy", password="safe-pass")
    client.force_login(user)

    get_response = client.get("/planner/", HTTP_HOST="localhost")
    assert get_response.status_code == 200
    token = str(get_response.context["idempotency_key"])

    with patch("planner.tasks.run_plan_pipeline.delay") as mocked_delay:
        with django_capture_on_commit_callbacks(execute=True):
            response = client.post("/planner/", data=_wizard_payload(idempotency_key=token), HTTP_HOST="localhost")

    assert response.status_code == 302
    assert PlanRequest.objects.filter(user=user).count() == 1
    plan = PlanRequest.objects.get(user=user)
    assert response.headers["Location"].endswith(f"/plans/{plan.id}/")
    mocked_delay.assert_called_once_with(str(plan.id))


@pytest.mark.django_db
def test_wizard_double_submit_is_idempotent(client, django_capture_on_commit_callbacks):
    _seed_airports()
    user = User.objects.create_user(username="wizard_double", password="safe-pass")
    client.force_login(user)

    get_response = client.get("/planner/", HTTP_HOST="localhost")
    token = str(get_response.context["idempotency_key"])
    payload = _wizard_payload(idempotency_key=token)

    with patch("planner.tasks.run_plan_pipeline.delay") as mocked_delay:
        with django_capture_on_commit_callbacks(execute=True):
            first = client.post("/planner/", data=payload, HTTP_HOST="localhost")
            second = client.post("/planner/", data=payload, HTTP_HOST="localhost")

    assert first.status_code == 302
    assert second.status_code == 302
    assert first.headers["Location"] == second.headers["Location"]
    assert PlanRequest.objects.filter(user=user).count() == 1
    mocked_delay.assert_called_once()


@pytest.mark.django_db
def test_wizard_rapid_consecutive_posts_do_not_error(client, django_capture_on_commit_callbacks):
    _seed_airports()
    user = User.objects.create_user(username="wizard_rapid", password="safe-pass")
    client.force_login(user)

    token = str(client.get("/planner/", HTTP_HOST="localhost").context["idempotency_key"])
    payload = _wizard_payload(idempotency_key=token)

    with patch("planner.tasks.run_plan_pipeline.delay") as mocked_delay:
        with django_capture_on_commit_callbacks(execute=True):
            for _ in range(8):
                response = client.post("/planner/", data=payload, HTTP_HOST="localhost")
                assert response.status_code == 302

    assert PlanRequest.objects.filter(user=user).count() == 1
    mocked_delay.assert_called_once()


@pytest.mark.django_db(transaction=True)
def test_create_plan_request_dispatches_pipeline_on_commit():
    _seed_airports()
    user = User.objects.create_user(username="wizard_on_commit", password="safe-pass")

    with patch("planner.tasks.run_plan_pipeline.delay") as mocked_delay:
        with transaction.atomic():
            plan = create_plan_request(
                user,
                _service_payload(),
                idempotency_key="wizard:on-commit-key",
            )
            assert mocked_delay.call_count == 0
        assert mocked_delay.call_count == 1
        mocked_delay.assert_called_with(str(plan.id))
