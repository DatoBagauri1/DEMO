from datetime import date
from unittest.mock import patch

import httpx
from django.core.cache import cache

from planner.services.travelpayouts.adapter import TravelpayoutsAdapter


class DummyResponse:
    def __init__(self, payload: dict, status_code: int = 200, url: str = "https://api.travelpayouts.com/test"):
        self._payload = payload
        self.status_code = status_code
        self.request = httpx.Request("GET", url)

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            response = httpx.Response(self.status_code, request=self.request)
            raise httpx.HTTPStatusError("Request failed", request=self.request, response=response)

    def json(self) -> dict:
        return self._payload


def _estimate_payload(adapter: TravelpayoutsAdapter):
    return adapter.estimate(
        origin_code="JFK",
        destination_code="CDG",
        destination_city="Paris",
        destination_country="FR",
        depart_date=date(2026, 7, 1),
        return_date=date(2026, 7, 8),
        travelers=2,
        tier="premium",
        tags=["culture", "food"],
        origin_coords=(40.6413, -73.7781),
        destination_coords=(49.0097, 2.5479),
        nonstop_likelihood=0.75,
        preferred_currency="USD",
    )


def test_travelpayouts_adapter_parses_live_prices(monkeypatch):
    cache.clear()
    monkeypatch.setenv("TRAVELPAYOUTS_ENABLED", "true")
    monkeypatch.setenv("TRAVELPAYOUTS_API_TOKEN", "token")
    monkeypatch.setenv("TRAVELPAYOUTS_BASE_CURRENCY", "USD")

    adapter = TravelpayoutsAdapter()
    responses = [
        DummyResponse({"data": {"CDG": {"0": {"price": 520}, "1": {"price": 590}}}}),
        DummyResponse({"data": [{"price": 540}, {"price": 610}], "meta": {"updated_at": "2026-06-01T10:00:00Z"}}),
        DummyResponse({"data": {"CDG": {"price": 560}}}),
    ]

    with patch("httpx.request", side_effect=responses):
        estimate = _estimate_payload(adapter)

    assert estimate.source == "travelpayouts"
    assert estimate.flight_min > 0
    assert estimate.flight_max >= estimate.flight_min
    assert estimate.endpoints["cheap"] == "ok"
    assert estimate.endpoints["calendar"] == "ok"


def test_travelpayouts_adapter_timeout_falls_back(monkeypatch):
    cache.clear()
    monkeypatch.setenv("TRAVELPAYOUTS_ENABLED", "true")
    monkeypatch.setenv("TRAVELPAYOUTS_API_TOKEN", "token")

    adapter = TravelpayoutsAdapter()
    with patch("httpx.request", side_effect=httpx.TimeoutException("timed out")):
        estimate = _estimate_payload(adapter)

    assert estimate.source == "fallback"
    assert estimate.error_type == "timeout"
    assert estimate.flight_min > 0
    assert estimate.hotel_nightly_min > 0


def test_travelpayouts_adapter_http_error_falls_back(monkeypatch):
    cache.clear()
    monkeypatch.setenv("TRAVELPAYOUTS_ENABLED", "true")
    monkeypatch.setenv("TRAVELPAYOUTS_API_TOKEN", "token")

    adapter = TravelpayoutsAdapter()

    def _http_503(*args, **kwargs):  # noqa: ANN002, ANN003
        return DummyResponse({}, status_code=503)

    with patch("httpx.request", side_effect=_http_503):
        estimate = _estimate_payload(adapter)

    assert estimate.source == "fallback"
    assert estimate.error_type in {"unknown", "rate_limit", "auth", "quota", "timeout"}
    assert any(status != "ok" for status in estimate.endpoints.values())
