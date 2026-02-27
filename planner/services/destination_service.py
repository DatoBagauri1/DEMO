from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

from planner.models import DestinationCandidate, PlanRequest
from planner.services.airports import airport_coordinates as dataset_airport_coordinates
from planner.services.airports import airport_timezone as dataset_airport_timezone
from planner.services.airports import get_airport, normalize_iata, resolve_origin_code as resolve_origin_from_dataset
from planner.services.geo import haversine_km
from planner.services.travelpayouts.fallbacks import airport_override_profile, country_default_profile


def airport_coordinates(airport_code: str) -> tuple[float, float] | None:
    return dataset_airport_coordinates(airport_code)


def resolve_origin_code(origin_input: str) -> str:
    return resolve_origin_from_dataset(origin_input)


def _timezone_offset_hours(timezone_name: str) -> float:
    if not timezone_name:
        return 0.0
    try:
        tz = ZoneInfo(timezone_name)
        now = datetime.now(tz)
        offset = now.utcoffset()
        if not offset:
            return 0.0
        return float(offset.total_seconds() / 3600)
    except Exception:  # noqa: BLE001
        return 0.0


def _rough_travel_time_minutes(origin_coords: tuple[float, float] | None, dest_coords: tuple[float, float] | None) -> int:
    if not origin_coords or not dest_coords:
        return 360
    distance = haversine_km(origin_coords[0], origin_coords[1], dest_coords[0], dest_coords[1])
    # Approximation: taxi/layover + cruise speed.
    return int(max(90, 80 + (distance / 780.0) * 60))


def _candidate_score(
    *,
    airport_name: str,
    origin_coords: tuple[float, float] | None,
    destination_coords: tuple[float, float] | None,
    origin_timezone: str,
    destination_timezone: str,
    max_duration_minutes: int | None,
) -> tuple[float, float, int]:
    distance_km = 3200.0
    if origin_coords and destination_coords:
        distance_km = float(
            haversine_km(
                origin_coords[0],
                origin_coords[1],
                destination_coords[0],
                destination_coords[1],
            ),
        )

    duration_minutes = _rough_travel_time_minutes(origin_coords, destination_coords)
    if max_duration_minutes and duration_minutes > max_duration_minutes:
        return -9999.0, distance_km, duration_minutes

    origin_offset = _timezone_offset_hours(origin_timezone)
    destination_offset = _timezone_offset_hours(destination_timezone)
    timezone_delta = abs(origin_offset - destination_offset)

    score = 0.0
    if "international" in airport_name.lower():
        score += 24.0

    if distance_km <= 1200:
        score += 16.0
    elif distance_km <= 3800:
        score += 26.0
    elif distance_km <= 8500:
        score += 18.0
    else:
        score += 6.0

    if timezone_delta <= 2:
        score += 18.0
    elif timezone_delta <= 5:
        score += 11.0
    else:
        score += 4.0

    # Favor practical, non-red-eye-ish duration windows.
    if duration_minutes <= 420:
        score += 17.0
    elif duration_minutes <= 720:
        score += 11.0
    else:
        score += 5.0

    return score, distance_km, duration_minutes


def _direct_airports(plan: PlanRequest) -> list[str]:
    selected: list[str] = []
    if plan.destination_iatas:
        selected.extend([normalize_iata(item) for item in plan.destination_iatas if normalize_iata(item)])
    if plan.destination_iata:
        selected.append(normalize_iata(plan.destination_iata))

    deduped: list[str] = []
    seen = set()
    for code in selected:
        if code in seen:
            continue
        seen.add(code)
        deduped.append(code)
    return deduped


def _explore_airports(plan: PlanRequest, max_items: int) -> list[str]:
    from planner.models import Airport

    origin = normalize_iata(plan.origin_iata or plan.origin_code)
    origin_airport = get_airport(origin)
    origin_coords = (float(origin_airport.latitude), float(origin_airport.longitude)) if origin_airport and origin_airport.latitude is not None and origin_airport.longitude is not None else None
    origin_timezone = origin_airport.timezone if origin_airport else ""

    explore = plan.explore_constraints or {}
    country_filter = (plan.destination_country or "").upper().strip()
    if country_filter in {"", "XX", "**"}:
        country_filter = str(explore.get("country_code") or "").upper().strip()

    max_duration = (plan.flight_filters or {}).get("max_duration_minutes")
    try:
        max_duration_minutes = int(max_duration) if max_duration is not None else None
    except (TypeError, ValueError):
        max_duration_minutes = None

    queryset = Airport.objects.exclude(iata=origin)
    if country_filter:
        queryset = queryset.filter(country_code=country_filter)

    airports = list(queryset.only("iata", "name", "city", "country", "country_code", "latitude", "longitude", "timezone")[:4000])

    ranked: list[tuple[float, str]] = []
    for airport in airports:
        if airport.latitude is None or airport.longitude is None:
            continue
        score, _, _ = _candidate_score(
            airport_name=airport.name,
            origin_coords=origin_coords,
            destination_coords=(float(airport.latitude), float(airport.longitude)),
            origin_timezone=origin_timezone,
            destination_timezone=airport.timezone,
            max_duration_minutes=max_duration_minutes,
        )
        if score < 0:
            continue
        ranked.append((score, airport.iata))

    ranked.sort(key=lambda item: (-item[0], item[1]))
    if ranked:
        return [code for _, code in ranked[:max_items]]

    # Fallback to deterministic first airports if heuristics remove all.
    return list(queryset.order_by("iata").values_list("iata", flat=True)[:max_items])


def _tags_for_airport(airport_code: str, country_code: str) -> tuple[str, list[str], float]:
    defaults = country_default_profile(country_code)
    override = airport_override_profile(airport_code)
    tier = str(override.get("tier") or defaults.get("tier") or "standard")
    tags = list(dict.fromkeys(override.get("tags") or defaults.get("tags") or ["culture", "food"]))
    nonstop_likelihood = float(override.get("nonstop_likelihood") or defaults.get("nonstop_likelihood") or 0.55)
    return tier, tags, nonstop_likelihood


def build_destination_candidates(plan: PlanRequest, max_items: int = 8) -> list[DestinationCandidate]:
    from planner.models import Airport

    plan.destination_candidates.all().delete()
    if plan.search_mode == PlanRequest.SearchMode.DIRECT:
        airport_codes = _direct_airports(plan)
    else:
        airport_codes = _explore_airports(plan, max_items=max_items)

    if not airport_codes:
        return []

    airports = {
        airport.iata: airport
        for airport in Airport.objects.filter(iata__in=airport_codes)
    }

    origin_code = normalize_iata(plan.origin_iata or plan.origin_code)
    origin_coords = airport_coordinates(origin_code)
    origin_timezone = dataset_airport_timezone(origin_code)

    records: list[DestinationCandidate] = []
    for rank, airport_code in enumerate(airport_codes[:max_items], start=1):
        airport = airports.get(airport_code)
        if not airport:
            continue

        tier, tags, nonstop_likelihood = _tags_for_airport(airport_code, airport.country_code)
        destination_coords = None
        if airport.latitude is not None and airport.longitude is not None:
            destination_coords = (float(airport.latitude), float(airport.longitude))

        score, distance_km, duration_minutes = _candidate_score(
            airport_name=airport.name,
            origin_coords=origin_coords,
            destination_coords=destination_coords,
            origin_timezone=origin_timezone,
            destination_timezone=airport.timezone,
            max_duration_minutes=None,
        )

        records.append(
            DestinationCandidate(
                plan=plan,
                country_code=(airport.country_code or "XX").upper()[:2],
                city_name=airport.city,
                airport_code=airport.iata,
                latitude=airport.latitude,
                longitude=airport.longitude,
                timezone=airport.timezone,
                rank=rank,
                metadata={
                    "tier": tier,
                    "tags": tags,
                    "nonstop_likelihood": nonstop_likelihood,
                    "airport_name": airport.name,
                    "country": airport.country,
                    "heuristic_score": round(score, 3),
                    "distance_km": round(distance_km, 2),
                    "estimated_duration_minutes": duration_minutes,
                },
            ),
        )

    return DestinationCandidate.objects.bulk_create(records)
