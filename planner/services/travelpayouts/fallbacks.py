from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from functools import lru_cache
from pathlib import Path

from planner.services.geo import haversine_km

BASELINE_PATH = Path(__file__).resolve().parents[2] / "data" / "pricing_baselines.json"


@dataclass
class FallbackPriceEstimate:
    flight_min: Decimal
    flight_max: Decimal
    hotel_nightly_min: Decimal
    hotel_nightly_max: Decimal
    distance_km: float
    distance_band: str
    travel_time_minutes: int
    nonstop_likelihood: float
    season_multiplier: float


@lru_cache(maxsize=1)
def load_pricing_baselines() -> dict:
    with BASELINE_PATH.open("r", encoding="utf-8-sig") as handle:
        return json.load(handle)


def season_multiplier_for_month(month: int) -> float:
    data = load_pricing_baselines().get("season_multipliers", {})
    return float(data.get(str(int(month)), 1.0))


def country_default_profile(country_code: str) -> dict:
    defaults = load_pricing_baselines().get("country_defaults", {})
    return defaults.get(country_code.upper(), {"tier": "standard", "tags": ["culture", "food"], "nonstop_likelihood": 0.55})


def airport_override_profile(airport_code: str) -> dict:
    overrides = load_pricing_baselines().get("airport_overrides", {})
    return overrides.get(airport_code.upper(), {})


def tier_profile(tier_name: str) -> dict:
    tiers = load_pricing_baselines().get("hotel_tiers", {})
    return tiers.get(tier_name, tiers.get("standard", {"nightly_min": 80, "nightly_max": 190, "star_rating": 3.6, "guest_rating": 8.0}))


def distance_profile(distance_km: float) -> dict:
    bands = load_pricing_baselines().get("distance_bands_km", [])
    for band in bands:
        if distance_km <= float(band.get("max_km", 0)):
            return band
    return bands[-1] if bands else {
        "band": "medium",
        "flight_min": 260,
        "flight_max": 680,
        "travel_time_hours": 5.0,
        "nonstop_likelihood": 0.72,
    }


def estimate_fallback_prices(
    *,
    origin_coords: tuple[float, float] | None,
    destination_coords: tuple[float, float] | None,
    depart_date: date,
    travelers: int,
    tier: str,
    nonstop_likelihood: float | None = None,
) -> FallbackPriceEstimate:
    if origin_coords and destination_coords:
        distance_km = float(
            haversine_km(
                origin_coords[0],
                origin_coords[1],
                destination_coords[0],
                destination_coords[1],
            ),
        )
    else:
        distance_km = 2400.0

    band = distance_profile(distance_km)
    season_multiplier = season_multiplier_for_month(depart_date.month)
    traveler_count = max(1, int(travelers))

    base_flight_min = Decimal(str(band.get("flight_min", 260)))
    base_flight_max = Decimal(str(band.get("flight_max", 680)))

    traveler_spread = Decimal("1") + (Decimal(str(max(0, traveler_count - 1))) * Decimal("0.09"))
    flight_min = (base_flight_min * Decimal(str(season_multiplier)) * traveler_count).quantize(Decimal("0.01"))
    flight_max = (base_flight_max * Decimal(str(season_multiplier)) * traveler_count * traveler_spread).quantize(Decimal("0.01"))

    hotel = tier_profile(tier)
    hotel_min_base = Decimal(str(hotel.get("nightly_min", 80)))
    hotel_max_base = Decimal(str(hotel.get("nightly_max", 190)))
    occupancy_factor = Decimal("1") + (Decimal(str(max(0, traveler_count - 2))) * Decimal("0.18"))

    hotel_nightly_min = (hotel_min_base * Decimal(str(season_multiplier)) * occupancy_factor).quantize(Decimal("0.01"))
    hotel_nightly_max = (hotel_max_base * Decimal(str(season_multiplier)) * occupancy_factor).quantize(Decimal("0.01"))

    profile_nonstop = float(band.get("nonstop_likelihood", 0.6))
    if nonstop_likelihood is not None:
        profile_nonstop = float(nonstop_likelihood)

    travel_minutes = int(float(band.get("travel_time_hours", 5.0)) * 60)

    return FallbackPriceEstimate(
        flight_min=flight_min,
        flight_max=flight_max,
        hotel_nightly_min=hotel_nightly_min,
        hotel_nightly_max=hotel_nightly_max,
        distance_km=distance_km,
        distance_band=str(band.get("band", "medium")),
        travel_time_minutes=max(90, travel_minutes),
        nonstop_likelihood=max(0.0, min(1.0, profile_nonstop)),
        season_multiplier=season_multiplier,
    )
