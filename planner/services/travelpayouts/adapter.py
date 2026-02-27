from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from django.utils import timezone

from planner.services.config import travelpayouts_base_currency, travelpayouts_enabled
from planner.services.providers.base import ProviderException
from planner.services.travelpayouts.client import TravelpayoutsClient
from planner.services.travelpayouts.fallbacks import estimate_fallback_prices
from planner.services.travelpayouts.types import CandidateEstimate

_PRICE_KEYS = {
    "price",
    "min_price",
    "max_price",
    "average_price",
    "avg_price",
    "value",
    "amount",
    "total_price",
}
_TIMESTAMP_HINTS = {"updated", "update", "fetched", "timestamp", "expires", "as_of"}


def _to_decimal(value: Any) -> Decimal | None:
    try:
        decimal_value = Decimal(str(value))
    except Exception:  # noqa: BLE001
        return None
    if decimal_value <= 0:
        return None
    if decimal_value > Decimal("50000"):
        return None
    return decimal_value


def _extract_price_values(node: Any) -> list[Decimal]:
    prices: list[Decimal] = []
    if isinstance(node, dict):
        for key, value in node.items():
            normalized_key = str(key).lower()
            if normalized_key in _PRICE_KEYS:
                decimal_value = _to_decimal(value)
                if decimal_value is not None:
                    prices.append(decimal_value)
            elif isinstance(value, (dict, list)):
                prices.extend(_extract_price_values(value))
    elif isinstance(node, list):
        for item in node:
            prices.extend(_extract_price_values(item))
    return prices


def _extract_timestamp_values(node: Any) -> list[datetime]:
    timestamps: list[datetime] = []
    if isinstance(node, dict):
        for key, value in node.items():
            key_text = str(key).lower()
            if any(hint in key_text for hint in _TIMESTAMP_HINTS):
                parsed = _parse_datetime(value)
                if parsed:
                    timestamps.append(parsed)
            if isinstance(value, (dict, list)):
                timestamps.extend(_extract_timestamp_values(value))
    elif isinstance(node, list):
        for item in node:
            timestamps.extend(_extract_timestamp_values(item))
    return timestamps


def _parse_datetime(value: Any) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    raw = value.strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if timezone.is_naive(parsed):
        return timezone.make_aware(parsed, timezone.utc)
    return parsed


def _extract_destination_prices(payload: dict[str, Any], destination_code: str, destination_city: str) -> list[Decimal]:
    data = payload.get("data")
    probes = [destination_code, destination_code.upper(), destination_city, destination_city.upper(), destination_city.lower()]
    collected: list[Decimal] = []

    if isinstance(data, dict):
        for probe in probes:
            if probe in data:
                collected.extend(_extract_price_values(data[probe]))

    if not collected:
        collected.extend(_extract_price_values(payload))

    return [price for price in collected if price >= Decimal("20")]


def _quantize_money(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"))


def _clamp(value: Decimal, low: Decimal, high: Decimal) -> Decimal:
    return max(low, min(high, value))


class TravelpayoutsAdapter:
    provider = "travelpayouts"

    def __init__(self, client: TravelpayoutsClient | None = None) -> None:
        self.client = client or TravelpayoutsClient()

    def estimate(
        self,
        *,
        origin_code: str,
        destination_code: str,
        destination_city: str,
        destination_country: str,
        depart_date: date,
        return_date: date | None,
        travelers: int,
        tier: str,
        tags: list[str],
        origin_coords: tuple[float, float] | None,
        destination_coords: tuple[float, float] | None,
        nonstop_likelihood: float | None,
        preferred_currency: str | None = None,
    ) -> CandidateEstimate:
        currency = (preferred_currency or travelpayouts_base_currency()).upper()

        fallback = estimate_fallback_prices(
            origin_coords=origin_coords,
            destination_coords=destination_coords,
            depart_date=depart_date,
            travelers=travelers,
            tier=tier,
            nonstop_likelihood=nonstop_likelihood,
        )

        endpoint_status: dict[str, str] = {}
        endpoint_payloads: dict[str, Any] = {}
        endpoint_latencies: list[int] = []
        live_prices: list[Decimal] = []
        freshness_hints: list[datetime] = []
        primary_error: ProviderException | None = None

        enabled = travelpayouts_enabled() and self.client.enabled
        if enabled:
            endpoint_calls = {
                "cheap": lambda: self.client.get_cheap_prices(
                    origin=origin_code,
                    destination=destination_code,
                    depart_date=depart_date,
                    return_date=return_date,
                    currency=currency,
                ),
                "calendar": lambda: self.client.get_calendar_prices(
                    origin=origin_code,
                    destination=destination_code,
                    depart_date=depart_date,
                    return_date=return_date,
                    currency=currency,
                ),
                "city_directions": lambda: self.client.get_city_directions(
                    origin=origin_code,
                    currency=currency,
                ),
            }
            for endpoint, handler in endpoint_calls.items():
                try:
                    payload, latency_ms = handler()
                    endpoint_status[endpoint] = "ok"
                    endpoint_payloads[endpoint] = payload
                    endpoint_latencies.append(latency_ms)
                    live_prices.extend(_extract_destination_prices(payload, destination_code, destination_city))
                    freshness_hints.extend(_extract_timestamp_values(payload))
                except ProviderException as exc:
                    endpoint_status[endpoint] = exc.error_type
                    endpoint_payloads[endpoint] = {
                        "error_type": exc.error_type,
                        "http_status": exc.http_status,
                    }
                    if primary_error is None:
                        primary_error = exc
        else:
            endpoint_status["travelpayouts"] = "disabled"
            endpoint_payloads["travelpayouts"] = {"reason": "TRAVELPAYOUTS_ENABLED=false or token missing"}

        source = "fallback"
        flight_min = fallback.flight_min
        flight_max = fallback.flight_max

        if live_prices:
            raw_min = min(live_prices)
            raw_max = max(live_prices)
            if raw_max <= raw_min:
                raw_max = raw_min * Decimal("1.22")

            bounded_min = _clamp(raw_min, fallback.flight_min * Decimal("0.55"), fallback.flight_max * Decimal("1.75"))
            bounded_max = _clamp(raw_max, bounded_min * Decimal("1.06"), fallback.flight_max * Decimal("2.20"))
            flight_min = _quantize_money(bounded_min)
            flight_max = _quantize_money(bounded_max)
            source = "travelpayouts"

        ratio = Decimal("1")
        fallback_mid = (fallback.flight_min + fallback.flight_max) / Decimal("2")
        if fallback_mid > 0:
            ratio = ((flight_min + flight_max) / Decimal("2")) / fallback_mid
        ratio = _clamp(ratio, Decimal("0.82"), Decimal("1.36"))

        hotel_nightly_min = _quantize_money(fallback.hotel_nightly_min * ratio)
        hotel_nightly_max = _quantize_money(fallback.hotel_nightly_max * ratio)

        freshness_at = max(freshness_hints) if freshness_hints else timezone.now()
        latency_ms = max(endpoint_latencies) if endpoint_latencies else (primary_error.latency_ms if primary_error else None)

        error_type: str | None = None
        http_status: int | None = None
        error_summary = ""
        if source == "fallback" and primary_error:
            error_type = primary_error.error_type
            http_status = primary_error.http_status
            error_summary = str(primary_error)

        raw_payload = {
            "origin_code": origin_code,
            "destination_code": destination_code,
            "destination_city": destination_city,
            "destination_country": destination_country,
            "tier": tier,
            "tags": tags,
            "fallback": {
                "flight_min": str(fallback.flight_min),
                "flight_max": str(fallback.flight_max),
                "hotel_nightly_min": str(fallback.hotel_nightly_min),
                "hotel_nightly_max": str(fallback.hotel_nightly_max),
                "distance_km": round(fallback.distance_km, 2),
                "distance_band": fallback.distance_band,
                "season_multiplier": fallback.season_multiplier,
                "nonstop_likelihood": fallback.nonstop_likelihood,
            },
            "live_price_points": [str(price) for price in live_prices[:40]],
            "endpoints": endpoint_payloads,
        }

        return CandidateEstimate(
            provider=self.provider,
            source=source,
            currency=currency,
            flight_min=flight_min,
            flight_max=flight_max,
            hotel_nightly_min=hotel_nightly_min,
            hotel_nightly_max=hotel_nightly_max,
            freshness_at=freshness_at,
            distance_km=fallback.distance_km,
            distance_band=fallback.distance_band,
            travel_time_minutes=fallback.travel_time_minutes,
            nonstop_likelihood=fallback.nonstop_likelihood,
            season_multiplier=fallback.season_multiplier,
            tier=tier,
            tags=tags,
            raw_payload=raw_payload,
            endpoints=endpoint_status,
            error_type=error_type,
            http_status=http_status,
            error_summary=error_summary[:500],
            latency_ms=latency_ms,
        )
