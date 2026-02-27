from __future__ import annotations

from datetime import date
from decimal import Decimal

from django.core.cache import cache

from planner.models import DestinationCandidate, PlanRequest
from planner.services.deeplinks import (
    build_flight_search_link,
    build_hotel_search_link,
    build_tour_search_link,
    resolve_partner_deeplink,
)
from planner.services.places import fetch_places
from planner.services.travelpayouts.types import CandidateEstimate
from planner.services.unsplash import get_destination_image


ENTITY_CACHE_TTL = 60 * 45


def _safe_money(raw: Decimal | float | str) -> str:
    try:
        value = Decimal(str(raw)).quantize(Decimal("0.01"))
        return f"{value}"
    except Exception:  # noqa: BLE001
        return ""


def _flight_entities(
    *,
    plan: PlanRequest,
    candidate: DestinationCandidate,
    estimate: CandidateEstimate,
    depart_date: date,
    return_date: date | None,
    count: int = 8,
) -> list[dict]:
    live_points = [Decimal(str(point)) for point in estimate.raw_payload.get("live_price_points", []) if str(point).strip()]
    if not live_points:
        live_points = [estimate.flight_min, estimate.flight_mid, estimate.flight_max]
    unique_prices = []
    seen = set()
    for point in sorted(live_points):
        key = str(point)
        if key in seen:
            continue
        seen.add(key)
        unique_prices.append(point)
    if not unique_prices:
        unique_prices = [estimate.flight_mid]

    image_url = get_destination_image(f"{candidate.city_name} airport")
    explicit_items = estimate.raw_payload.get("flight_items")
    if not isinstance(explicit_items, list):
        explicit_items = []
    stops_by_band = {"short": 0, "medium": 1, "long": 1, "ultra_long": 2}
    default_stops = stops_by_band.get(str(estimate.distance_band or "medium"), 1)
    default_duration_minutes = max(90, int(estimate.travel_time_minutes or 0))
    entities = []
    for idx, price in enumerate(unique_prices[:count], start=1):
        explicit = explicit_items[idx - 1] if idx - 1 < len(explicit_items) and isinstance(explicit_items[idx - 1], dict) else {}
        search_url = build_flight_search_link(
            origin=plan.origin_code,
            destination=candidate.airport_code,
            depart_date=depart_date,
            return_date=return_date,
            travelers=plan.total_travelers,
            plan_id=str(plan.id),
            destination_label=f"{candidate.city_name}-{candidate.country_code}",
        )
        item_url = str(explicit.get("deeplink_url") or explicit.get("outbound_url") or explicit.get("item_url") or "").strip()
        outbound_url, link_type, fallback_search = resolve_partner_deeplink(
            item_url=item_url,
            search_url=search_url,
            provider=estimate.provider,
            plan_id=str(plan.id),
            link_type="flight",
            destination=f"{candidate.city_name}-{candidate.country_code}",
        )
        stable_id = str(
            explicit.get("offer_id")
            or explicit.get("external_offer_id")
            or f"{estimate.provider}:flight:{candidate.airport_code}:{depart_date:%Y%m%d}:{idx}",
        )
        airline_codes = [str(code).upper() for code in (explicit.get("airline_codes") or []) if str(code).strip()]
        try:
            stops = max(0, int(explicit.get("stops", default_stops)))
        except (TypeError, ValueError):
            stops = default_stops
        try:
            duration_minutes = max(0, int(explicit.get("duration_minutes", default_duration_minutes)))
        except (TypeError, ValueError):
            duration_minutes = default_duration_minutes
        entities.append(
            {
                "title": str(explicit.get("title") or f"{plan.origin_code} to {candidate.airport_code} flight {idx}"),
                "name": str(explicit.get("name") or explicit.get("title") or f"{plan.origin_code} to {candidate.airport_code} flight {idx}"),
                "price": _safe_money(price),
                "currency": estimate.currency,
                "link": outbound_url,
                "outbound_url": outbound_url,
                "deeplink_url": outbound_url,
                "image_url": image_url,
                "provider": estimate.provider,
                "kind": "flight",
                "description": str(explicit.get("description") or f"Concrete flight offer option for {candidate.city_name}."),
                "stable_id": stable_id,
                "link_type": link_type,
                "fallback_search": fallback_search,
                "confidence": 0.95 if link_type == "item" else 0.88,
                "rationale": (
                    "Item-level flight deeplink generated from provider offer."
                    if link_type == "item"
                    else "Parameterized route/date search deeplink fallback generated from provider estimate."
                ),
                "stops": stops,
                "duration_minutes": duration_minutes,
                "airline_codes": airline_codes,
            },
        )
    return entities


def _hotel_entities(
    *,
    plan: PlanRequest,
    candidate: DestinationCandidate,
    estimate: CandidateEstimate,
    depart_date: date,
    return_date: date | None,
    count: int = 10,
) -> list[dict]:
    labels = [
        "Central Hotel",
        "Riverside Suites",
        "Old Town Residence",
        "Boutique Stay",
        "Grand Plaza Hotel",
        "Park View Hotel",
        "Harbor Lights Hotel",
        "City Gate Inn",
        "Skyline Retreat",
        "Heritage Rooms",
        "Metropolitan House",
        "Garden Court Hotel",
    ]
    image_url = get_destination_image(f"{candidate.city_name} hotel")
    nightly_min = estimate.hotel_nightly_min
    nightly_max = estimate.hotel_nightly_max
    selected_nights = 1
    if return_date and return_date > depart_date:
        selected_nights = max(1, int((return_date - depart_date).days))
    explicit_items = estimate.raw_payload.get("hotel_items")
    if not isinstance(explicit_items, list):
        explicit_items = []
    entities = []
    for idx, label in enumerate(labels[:count], start=1):
        explicit = explicit_items[idx - 1] if idx - 1 < len(explicit_items) and isinstance(explicit_items[idx - 1], dict) else {}
        ratio = Decimal("0.92") + Decimal("0.03") * Decimal(str(idx))
        nightly = ((nightly_min + nightly_max) / Decimal("2") * ratio).quantize(Decimal("0.01"))
        total_stay = (nightly * Decimal(str(selected_nights))).quantize(Decimal("0.01"))
        search_url = build_hotel_search_link(
            city=candidate.city_name,
            country_code=candidate.country_code,
            checkin=depart_date,
            checkout=return_date or depart_date,
            adults=max(1, int(plan.adults or plan.total_travelers)),
            plan_id=str(plan.id),
        )
        item_url = str(explicit.get("deeplink_url") or explicit.get("outbound_url") or explicit.get("item_url") or "").strip()
        outbound_url, link_type, fallback_search = resolve_partner_deeplink(
            item_url=item_url,
            search_url=search_url,
            provider=estimate.provider,
            plan_id=str(plan.id),
            link_type="hotel",
            destination=f"{candidate.city_name}-{candidate.country_code}",
        )
        stable_id = str(
            explicit.get("provider_property_id")
            or explicit.get("property_id")
            or f"search:{estimate.provider}:hotel:{candidate.airport_code}:{idx}",
        )
        hotel_name = str(explicit.get("name") or f"{candidate.city_name} {label}")
        entities.append(
            {
                "title": hotel_name,
                "name": hotel_name,
                "price": _safe_money(total_stay),
                "currency": estimate.currency,
                "link": outbound_url,
                "outbound_url": outbound_url,
                "deeplink_url": outbound_url,
                "image_url": image_url,
                "provider": estimate.provider,
                "kind": "hotel",
                "description": str(explicit.get("description") or f"Specific hotel option in {candidate.city_name} for selected nights."),
                "stable_id": stable_id,
                "provider_property_id": stable_id,
                "link_type": link_type,
                "fallback_search": fallback_search,
                "confidence": 0.9 if link_type == "item" else 0.55,
                "rationale": (
                    "Item-level hotel property deeplink generated from provider data."
                    if link_type == "item"
                    else "Specific property deeplink unavailable; using dated city hotel search fallback."
                ),
                "nightly_price": _safe_money(nightly),
                "total_stay_price": _safe_money(total_stay),
                "nights": selected_nights,
            },
        )
    return entities


def _tour_entities(*, plan: PlanRequest, candidate: DestinationCandidate, count: int = 7) -> list[dict]:
    templates = [
        "Walking Highlights Tour",
        "Food and Local Market Tour",
        "Museum and Culture Pass",
        "Sunset Panorama Experience",
        "Historic District Tour",
        "River or Coast Cruise",
        "Day Trip Essentials",
        "Street Art Route",
        "Old City Guided Visit",
        "Night Lights Experience",
    ]
    image_url = get_destination_image(f"{candidate.city_name} tours")
    explicit_items = (candidate.metadata or {}).get("tour_items")
    if not isinstance(explicit_items, list):
        explicit_items = []
    entities = []
    for idx, title in enumerate(templates[:count], start=1):
        explicit = explicit_items[idx - 1] if idx - 1 < len(explicit_items) and isinstance(explicit_items[idx - 1], dict) else {}
        search_url = build_tour_search_link(
            city=f"{candidate.city_name} {title}",
            country_code=candidate.country_code,
            plan_id=str(plan.id),
        )
        item_url = str(explicit.get("deeplink_url") or explicit.get("outbound_url") or explicit.get("item_url") or "").strip()
        outbound_url, link_type, fallback_search = resolve_partner_deeplink(
            item_url=item_url,
            search_url=search_url,
            provider=str(explicit.get("provider") or "travelpayouts"),
            plan_id=str(plan.id),
            link_type="tour",
            destination=f"{candidate.city_name}-{candidate.country_code}",
        )
        stable_id = str(
            explicit.get("product_id")
            or explicit.get("external_product_id")
            or f"search:tour:{candidate.airport_code}:{idx}",
        )
        entities.append(
            {
                "title": str(explicit.get("title") or f"{candidate.city_name} {title}"),
                "name": str(explicit.get("name") or explicit.get("title") or f"{candidate.city_name} {title}"),
                "price": str(explicit.get("price") or ""),
                "currency": str(explicit.get("currency") or ""),
                "link": outbound_url,
                "outbound_url": outbound_url,
                "deeplink_url": outbound_url,
                "image_url": image_url,
                "provider": str(explicit.get("provider") or "travelpayouts"),
                "kind": "tour",
                "description": str(explicit.get("description") or f"Popular activity idea in {candidate.city_name}."),
                "stable_id": stable_id,
                "link_type": link_type,
                "fallback_search": fallback_search,
                "confidence": 0.85 if link_type == "item" else 0.45,
                "rationale": (
                    "Item-level tour product deeplink generated from provider data."
                    if link_type == "item"
                    else "Product-level tour deeplink unavailable; using curated activity search fallback."
                ),
            },
        )
    return entities


def _place_entities(*, candidate: DestinationCandidate, count: int = 12) -> list[dict]:
    return fetch_places(
        city=candidate.city_name,
        country=candidate.country_code,
        latitude=candidate.latitude,
        longitude=candidate.longitude,
        limit=max(8, min(15, count)),
    )


def build_candidate_entities(
    *,
    plan: PlanRequest,
    candidate: DestinationCandidate,
    estimate: CandidateEstimate,
    depart_date: date,
    return_date: date | None,
) -> dict[str, list[dict]]:
    cache_key = (
        "entities:"
        f"{plan.origin_code}:{candidate.airport_code}:{depart_date}:{return_date}:"
        f"{plan.total_travelers}:{plan.search_currency}"
    )
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    payload = {
        "flights": _flight_entities(
            plan=plan,
            candidate=candidate,
            estimate=estimate,
            depart_date=depart_date,
            return_date=return_date,
            count=8,
        ),
        "hotels": _hotel_entities(
            plan=plan,
            candidate=candidate,
            estimate=estimate,
            depart_date=depart_date,
            return_date=return_date,
            count=12,
        ),
        "tours": _tour_entities(plan=plan, candidate=candidate, count=8),
        "places": _place_entities(candidate=candidate, count=12),
    }
    cache.set(cache_key, payload, ENTITY_CACHE_TTL)
    return payload


def build_flight_entities_for_candidate(
    *,
    plan: PlanRequest,
    candidate: DestinationCandidate,
    estimate: CandidateEstimate,
    depart_date: date,
    return_date: date | None,
    count: int = 8,
) -> list[dict]:
    return _flight_entities(
        plan=plan,
        candidate=candidate,
        estimate=estimate,
        depart_date=depart_date,
        return_date=return_date,
        count=count,
    )


def build_hotel_entities_for_candidate(
    *,
    plan: PlanRequest,
    candidate: DestinationCandidate,
    estimate: CandidateEstimate,
    depart_date: date,
    return_date: date | None,
    count: int = 10,
) -> list[dict]:
    return _hotel_entities(
        plan=plan,
        candidate=candidate,
        estimate=estimate,
        depart_date=depart_date,
        return_date=return_date,
        count=count,
    )


def build_tour_entities_for_candidate(*, plan: PlanRequest, candidate: DestinationCandidate, count: int = 7) -> list[dict]:
    return _tour_entities(plan=plan, candidate=candidate, count=count)


def build_place_entities_for_candidate(*, candidate: DestinationCandidate, count: int = 12) -> list[dict]:
    return _place_entities(candidate=candidate, count=count)


def fallback_image_for_city(city: str) -> str:
    return get_destination_image(f"{city} travel")
