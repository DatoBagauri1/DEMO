from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from zoneinfo import ZoneInfo
import logging

from django.db import transaction
from django.utils import timezone

from planner.models import FlightOption, HotelOption, PackageOption, PlanRequest, TourOption
from planner.services.deeplinks import build_tour_search_link
from planner.services.entities import fallback_image_for_city
from planner.services.fx import convert_decimal, quantize_money, to_minor_units
from planner.services.scoring import score_package

_TOUR_ESTIMATE_USD_BY_TIER = {
    "budget": Decimal("28.00"),
    "standard": Decimal("45.00"),
    "premium": Decimal("72.00"),
    "luxury": Decimal("110.00"),
}
_TOUR_DISTANCE_MULTIPLIER = {
    "short": Decimal("0.90"),
    "medium": Decimal("1.00"),
    "long": Decimal("1.12"),
    "ultra_long": Decimal("1.20"),
}
_CHILD_TOUR_FACTOR = Decimal("0.65")
logger = logging.getLogger(__name__)


def _sort_key(sort_mode: str):
    if sort_mode == "budget_first":
        return lambda item: (-item["price_score"], item["package_total"], -item["score"])
    if sort_mode == "cheapest":
        return lambda item: (item["package_total"], -item["score"])
    if sort_mode == "fastest":
        return lambda item: (item["flight"].duration_minutes or 0, item["package_total"])
    if sort_mode == "fewest_stops":
        return lambda item: (item["flight"].stops or 99, item["flight"].duration_minutes or 0, item["package_total"])
    if sort_mode == "family_friendly":
        return lambda item: (-item["family_friendly_score"], item["package_total"])
    if sort_mode == "best_hotel":
        return lambda item: (-item["quality_score"], item["package_total"])
    if sort_mode == "best_value":
        return lambda item: (-item["price_score"], -item["score"], item["package_total"])
    return lambda item: (-item["price_score"], item["package_total"], -item["score"])


def _as_decimal(raw: dict, key: str, fallback: Decimal) -> Decimal:
    value = raw.get(key)
    if value in (None, ""):
        return fallback
    try:
        return quantize_money(Decimal(str(value)))
    except Exception:  # noqa: BLE001
        return fallback


def _quantize(value: Decimal) -> Decimal:
    return quantize_money(Decimal(str(value)))


def _decimal_str(value: Decimal | int | float | str) -> str:
    return f"{_quantize(Decimal(str(value)))}"


def _timezone_offset_hours(timezone_name: str) -> float:
    if not timezone_name:
        return 0.0
    try:
        now = timezone.now().astimezone(ZoneInfo(timezone_name))
        offset = now.utcoffset()
        if not offset:
            return 0.0
        return float(offset.total_seconds() / 3600)
    except Exception:  # noqa: BLE001
        return 0.0


def _option_link_type(option) -> str:  # noqa: ANN001
    return str(getattr(option, "link_type", "") or (option.raw_payload or {}).get("link_type") or "search").strip().lower()


def _option_link_confidence(option) -> float:  # noqa: ANN001
    raw = getattr(option, "link_confidence", None)
    if raw is None:
        raw = (option.raw_payload or {}).get("link_confidence", 0.5)
    try:
        return float(raw)
    except (TypeError, ValueError):
        return 0.5


def _option_link_rationale(option) -> str:  # noqa: ANN001
    return str(getattr(option, "link_rationale", "") or (option.raw_payload or {}).get("link_rationale") or "").strip()


def _option_fallback_search(option) -> bool:  # noqa: ANN001
    payload = option.raw_payload or {}
    if "fallback_search" in payload:
        return bool(payload.get("fallback_search"))
    return _option_link_type(option) != "item"


def _flight_component_payload(plan: PlanRequest, candidate, flight: FlightOption, currency: str) -> dict:  # noqa: ANN001
    outbound_url = str(flight.deeplink_url or "").strip()
    link_type = _option_link_type(flight)
    stable_id = str((flight.raw_payload or {}).get("stable_offer_id") or flight.external_offer_id or flight.id)
    display_price = convert_decimal(Decimal(str(flight.total_price)), flight.currency, currency)
    return {
        "id": str(flight.id),
        "stable_id": stable_id,
        "provider": flight.provider,
        "outbound_url": outbound_url,
        "link": outbound_url,
        "deeplink_url": outbound_url,
        "link_type": link_type,
        "fallback_search": _option_fallback_search(flight),
        "confidence": round(_option_link_confidence(flight), 2),
        "rationale": _option_link_rationale(flight) or "Flight deeplink routed to partner.",
        "name": f"{plan.origin_code} to {candidate.airport_code}",
        "title": f"{plan.origin_code} to {candidate.airport_code}",
        "currency": currency,
        "price": _decimal_str(display_price),
        "stops": int(flight.stops or 0),
        "duration_minutes": int(flight.duration_minutes or 0),
        "airline_codes": list(flight.airline_codes or []),
        "kind": "flight",
        "image_url": fallback_image_for_city(candidate.city_name),
    }


def _hotel_component_payload(candidate, hotel: HotelOption, currency: str) -> dict:  # noqa: ANN001
    outbound_url = str(hotel.deeplink_url or "").strip()
    link_type = _option_link_type(hotel)
    stable_id = str(hotel.provider_property_id or (hotel.raw_payload or {}).get("provider_property_id") or hotel.external_offer_id or hotel.id)
    display_price = convert_decimal(Decimal(str(hotel.total_price)), hotel.currency, currency)
    return {
        "id": str(hotel.id),
        "stable_id": stable_id,
        "provider_property_id": stable_id,
        "provider": hotel.provider,
        "outbound_url": outbound_url,
        "link": outbound_url,
        "deeplink_url": outbound_url,
        "link_type": link_type,
        "fallback_search": _option_fallback_search(hotel),
        "confidence": round(_option_link_confidence(hotel), 2),
        "rationale": _option_link_rationale(hotel) or "Hotel deeplink routed to partner.",
        "name": hotel.name,
        "title": hotel.name,
        "currency": currency,
        "price": _decimal_str(display_price),
        "star_rating": float(hotel.star_rating or 0),
        "guest_rating": float(hotel.guest_rating or 0),
        "neighborhood": str(hotel.neighborhood or ""),
        "latitude": hotel.latitude,
        "longitude": hotel.longitude,
        "kind": "hotel",
        "image_url": fallback_image_for_city(candidate.city_name),
    }


def _tour_has_explicit_price(tour: TourOption) -> bool:
    try:
        return Decimal(str(tour.total_price or "0")) > Decimal("0.00")
    except Exception:  # noqa: BLE001
        return False


def _estimated_tour_unit_in_currency(plan: PlanRequest, candidate, target_currency: str) -> Decimal:  # noqa: ANN001
    metadata = dict(candidate.metadata or {})
    tier = str(metadata.get("tier") or "standard").strip().lower()
    base_per_traveler_usd = _TOUR_ESTIMATE_USD_BY_TIER.get(tier, _TOUR_ESTIMATE_USD_BY_TIER["standard"])

    distance_band = str(metadata.get("distance_band") or "medium").strip().lower()
    distance_multiplier = _TOUR_DISTANCE_MULTIPLIER.get(distance_band, Decimal("1.00"))

    adults = max(1, int(plan.adults or plan.travelers or 1))
    children = max(0, int(plan.children or 0))
    traveler_units = Decimal(str(adults)) + (Decimal(str(children)) * _CHILD_TOUR_FACTOR)

    estimate_usd = _quantize(base_per_traveler_usd * distance_multiplier * traveler_units)
    return convert_decimal(estimate_usd, "USD", target_currency)


def _tour_component_payload(plan: PlanRequest, candidate, tour: TourOption, target_currency: str) -> dict:  # noqa: ANN001
    outbound_url = str(tour.deeplink_url or "").strip()
    link_type = _option_link_type(tour)
    image_url = str((tour.raw_payload or {}).get("image_url") or fallback_image_for_city(candidate.city_name))
    has_explicit_price = _tour_has_explicit_price(tour)
    display_amount = _tour_total_in_currency(tour, target_currency)
    is_estimated = not has_explicit_price and display_amount > Decimal("0.00")
    return {
        "id": str(tour.id),
        "stable_id": str(tour.external_product_id or tour.id),
        "provider": tour.provider,
        "outbound_url": outbound_url,
        "link": outbound_url,
        "deeplink_url": outbound_url,
        "link_type": link_type,
        "fallback_search": _option_fallback_search(tour),
        "confidence": round(_option_link_confidence(tour), 2),
        "rationale": _option_link_rationale(tour)
        or ("Tour price estimated from destination tier and traveler mix." if is_estimated else "Tour deeplink routed to partner."),
        "name": tour.name,
        "title": tour.name,
        "currency": target_currency if display_amount else "",
        "price": _decimal_str(display_amount) if display_amount else "",
        "is_estimated": is_estimated,
        "kind": "tour",
        "description": str((tour.raw_payload or {}).get("description") or ""),
        "image_url": image_url,
    }


def _tour_total_in_currency(
    tour: TourOption,
    target_currency: str,
    *,
    allow_estimate: bool = False,
    plan: PlanRequest | None = None,
    candidate=None,  # noqa: ANN001
) -> Decimal:
    if _tour_has_explicit_price(tour):
        source_currency = (tour.currency or target_currency or "USD").upper()
        return convert_decimal(Decimal(str(tour.total_price)), source_currency, target_currency)
    if allow_estimate and plan is not None and candidate is not None:
        return _estimated_tour_unit_in_currency(plan, candidate, target_currency)
    return Decimal("0.00")


def _candidate_place_entities(candidate) -> list[dict]:  # noqa: ANN001
    entities = ((candidate.metadata or {}).get("entities") or {}).get("places") or []
    normalized: list[dict] = []
    for raw in entities:
        if not isinstance(raw, dict):
            continue
        link = str(raw.get("link") or raw.get("outbound_url") or "").strip()
        title = str(raw.get("title") or raw.get("name") or "").strip()
        if not title:
            continue
        provider = str(raw.get("provider") or "places")
        is_search = provider == "fallback" or "google.com/search" in link
        normalized.append(
            {
                **raw,
                "title": title,
                "name": title,
                "link": link,
                "outbound_url": link,
                "deeplink_url": link,
                "link_type": str(raw.get("link_type") or ("search" if is_search else "item")),
                "fallback_search": bool(raw.get("fallback_search")) if "fallback_search" in raw else bool(is_search),
                "confidence": raw.get("confidence", 0.65 if not is_search else 0.4),
                "rationale": str(
                    raw.get("rationale")
                    or ("Search-link fallback because Wikimedia data was unavailable." if is_search else "Wikimedia place item deeplink.")
                ),
                "stable_id": str(raw.get("stable_id") or raw.get("pageid") or title.lower().replace(" ", "-")),
            },
        )
    return normalized


def _default_entities(
    plan: PlanRequest,
    candidate,
    item: dict,
    target_currency: str,
) -> tuple[list[dict], list[dict], list[dict], list[dict]]:  # noqa: ANN001
    flights_payload = [_flight_component_payload(plan, candidate, item["flight"], target_currency)]
    hotels_payload = [_hotel_component_payload(candidate, item["hotel"], target_currency)]
    tours_payload = [
        _tour_component_payload(plan, candidate, tour, target_currency)
        for tour in item.get("selected_tours", [])
    ]
    if not tours_payload:
        fallback_image = fallback_image_for_city(candidate.city_name)
        fallback_link = build_tour_search_link(
            city=candidate.city_name,
            country_code=candidate.country_code,
            plan_id=str(plan.id),
        )
        tours_payload = [
            {
                "title": f"{candidate.city_name} tours search",
                "name": f"{candidate.city_name} tours search",
                "price": "",
                "currency": "",
                "link": fallback_link,
                "outbound_url": fallback_link,
                "deeplink_url": fallback_link,
                "image_url": fallback_image,
                "provider": "travelpayouts",
                "kind": "tour",
                "description": "Tour search fallback.",
                "stable_id": f"search:tour:{candidate.airport_code}:fallback",
                "link_type": "search",
                "fallback_search": True,
                "confidence": 0.35,
                "rationale": "No tour items were persisted; using search fallback.",
            },
        ]
    places_payload = _candidate_place_entities(candidate)
    if not places_payload:
        fallback_image = fallback_image_for_city(candidate.city_name)
        places_payload = [
            {
                "title": f"{candidate.city_name} city center",
                "name": f"{candidate.city_name} city center",
                "price": "",
                "currency": "",
                "link": f"https://www.google.com/search?q={candidate.city_name}+must+see",
                "outbound_url": f"https://www.google.com/search?q={candidate.city_name}+must+see",
                "deeplink_url": f"https://www.google.com/search?q={candidate.city_name}+must+see",
                "image_url": fallback_image,
                "provider": "fallback",
                "kind": "place",
                "description": "Popular area fallback.",
                "stable_id": f"search:place:{candidate.airport_code}:center",
                "link_type": "search",
                "fallback_search": True,
                "confidence": 0.3,
                "rationale": "Places source unavailable; using generic search fallback.",
            },
        ]
    return flights_payload, hotels_payload, tours_payload, places_payload


def _candidate_entities_map(candidate) -> dict:
    return dict((candidate.metadata or {}).get("entities") or {})


def _merge_selected_first(selected_payload: dict, extra_items: list[dict], max_items: int) -> list[dict]:
    selected_link = str(selected_payload.get("outbound_url") or selected_payload.get("link") or "")
    merged = [selected_payload]
    for raw in extra_items or []:
        if not isinstance(raw, dict):
            continue
        link = str(raw.get("outbound_url") or raw.get("link") or "").strip()
        if link and selected_link and link == selected_link:
            continue
        item = dict(raw)
        if "outbound_url" not in item:
            item["outbound_url"] = link
        if "link" not in item:
            item["link"] = link
        if "deeplink_url" not in item:
            item["deeplink_url"] = link
        if "link_type" not in item:
            item["link_type"] = "search"
        if "fallback_search" not in item:
            item["fallback_search"] = str(item.get("link_type") or "search") != "item"
        if "confidence" not in item:
            item["confidence"] = 0.5
        if "rationale" not in item:
            item["rationale"] = "Additional entity from candidate metadata."
        merged.append(item)
        if len(merged) >= max_items:
            break
    return merged


def _tour_bundle_variants(tours: list[TourOption], max_bundles: int = 5) -> list[list[TourOption]]:
    if not tours:
        return [[]]

    top = tours[:4]
    candidates: list[list[TourOption]] = [[]]
    if len(top) >= 1:
        candidates.append([top[0]])
    if len(top) >= 2:
        candidates.extend([[top[1]], top[:2]])
    if len(top) >= 3:
        candidates.extend([[top[2]], top[:3], [top[1], top[2]]])
    if len(top) >= 4:
        candidates.append([top[2], top[3]])

    deduped: list[list[TourOption]] = []
    seen: set[tuple[str, ...]] = set()
    for bundle in candidates:
        if len(bundle) > 3:
            continue
        key = tuple(str(item.id) for item in bundle)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(bundle)
        if len(deduped) >= max_bundles:
            break
    return deduped or [[]]


def _norm_text(value) -> str:  # noqa: ANN001
    return str(value or "").strip().lower()


def _norm_link(value) -> str:  # noqa: ANN001
    return str(value or "").strip()


def _flight_combo_signature(flight: FlightOption) -> tuple:
    raw = flight.raw_payload or {}
    stable_id = str(raw.get("stable_offer_id") or "")
    return (
        _norm_text(flight.provider),
        _norm_text(flight.origin_airport),
        _norm_text(flight.destination_airport),
        _decimal_str(flight.total_price),
        _norm_text(flight.currency),
        int(flight.stops or 0),
        int(flight.duration_minutes or 0),
        tuple(str(code).upper() for code in (flight.airline_codes or [])),
        _norm_link(flight.deeplink_url),
        stable_id,
    )


def _hotel_combo_signature(hotel: HotelOption) -> tuple:
    raw = hotel.raw_payload or {}
    property_id = str(hotel.provider_property_id or raw.get("provider_property_id") or "")
    return (
        _norm_text(hotel.provider),
        _norm_text(hotel.name),
        property_id,
        _decimal_str(hotel.total_price),
        _norm_text(hotel.currency),
        round(float(hotel.star_rating or 0), 2),
        round(float(hotel.guest_rating or 0), 2),
        _norm_text(hotel.neighborhood),
        _norm_link(hotel.deeplink_url),
    )


def _tour_combo_signature(tour: TourOption) -> tuple:
    return (
        _norm_text(tour.provider),
        _norm_text(tour.name),
        _norm_text(tour.external_product_id),
        _decimal_str(tour.total_price or Decimal("0.00")),
        _norm_text(tour.currency),
        _norm_link(tour.deeplink_url),
    )


def _combination_signature(item: dict) -> tuple:
    candidate = item["candidate"]
    return (
        _norm_text(candidate.airport_code),
        _norm_text(candidate.city_name),
        _flight_combo_signature(item["flight"]),
        _hotel_combo_signature(item["hotel"]),
        tuple(_tour_combo_signature(tour) for tour in item.get("selected_tours", [])),
        _decimal_str(item["exact_flight_total"]),
        _decimal_str(item["exact_hotel_total"]),
        _decimal_str(item["tours_total"]),
        _decimal_str(item["package_total"]),
    )


def _dedupe_sorted_combinations(combinations: list[dict], max_packages: int) -> list[dict]:
    selected: list[dict] = []
    seen: set[tuple] = set()
    for item in combinations:
        signature = _combination_signature(item)
        if signature in seen:
            continue
        seen.add(signature)
        selected.append(item)
        if len(selected) >= max_packages:
            break
    return selected


def build_packages_for_plan(
    plan: PlanRequest,
    sort_mode: str = "budget_first",
    max_packages: int = 10,
    flights_per_city: int = 3,
    hotels_per_city: int = 3,
) -> list[PackageOption]:
    PackageOption.objects.filter(plan=plan).delete()
    target_currency = (plan.search_currency or "USD").upper()

    flights_by_candidate: dict[int, list[FlightOption]] = defaultdict(list)
    hotels_by_candidate: dict[int, list[HotelOption]] = defaultdict(list)
    tours_by_candidate: dict[int, list[TourOption]] = defaultdict(list)

    for flight in plan.flight_options.select_related("candidate").order_by("total_price", "created_at"):
        flights_by_candidate[flight.candidate_id].append(flight)

    for hotel in plan.hotel_options.select_related("candidate").order_by("total_price", "created_at"):
        hotels_by_candidate[hotel.candidate_id].append(hotel)

    for tour in plan.tour_options.select_related("candidate").order_by("total_price", "created_at"):
        tours_by_candidate[tour.candidate_id].append(tour)

    nights_low = max(1, int(plan.trip_length_min or plan.nights_min or 1))
    nights_high = max(nights_low, int(plan.trip_length_max or plan.nights_max or nights_low))
    resolved_depart, resolved_return = plan.resolve_dates()
    selected_nights = max(1, int((resolved_return - resolved_depart).days)) if resolved_return > resolved_depart else nights_low
    budget_minor = 0
    preferences = plan.preference_weights or {}

    combinations: list[dict] = []
    origin_tz = str((plan.explore_constraints or {}).get("origin_timezone") or "")
    for candidate in plan.destination_candidates.all():
        flights = flights_by_candidate.get(candidate.id, [])[:flights_per_city]
        hotels = hotels_by_candidate.get(candidate.id, [])[:hotels_per_city]
        tours = tours_by_candidate.get(candidate.id, [])
        if not flights or not hotels:
            continue

        tags = [str(tag).lower() for tag in (candidate.metadata or {}).get("tags", [])]
        for flight in flights:
            flight_raw = flight.raw_payload or {}
            flight_min = _as_decimal(flight_raw, "estimated_min", Decimal(str(flight.total_price)))
            flight_max = _as_decimal(flight_raw, "estimated_max", Decimal(str(flight.total_price)))

            for hotel in hotels:
                hotel_raw = hotel.raw_payload or {}
                hotel_nightly_min = _as_decimal(hotel_raw, "nightly_min", Decimal(str(hotel.total_price)) / max(nights_low, 1))
                hotel_nightly_max = _as_decimal(hotel_raw, "nightly_max", Decimal(str(hotel.total_price)) / max(nights_high, 1))

                est_flight_min = convert_decimal(flight_min, flight.currency, target_currency)
                est_flight_max = convert_decimal(flight_max, flight.currency, target_currency)
                est_hotel_min = convert_decimal(hotel_nightly_min, hotel.currency, target_currency)
                est_hotel_max = convert_decimal(hotel_nightly_max, hotel.currency, target_currency)

                for selected_tours in _tour_bundle_variants(tours):
                    if selected_tours:
                        tour_totals = [_tour_total_in_currency(tour, target_currency) for tour in selected_tours]
                        optional_tours_total = _quantize(sum(tour_totals, Decimal("0.00")))
                    else:
                        optional_tours_total = Decimal("0.00")
                    tours_total = Decimal("0.00")
                    estimated_tours_used = False

                    exact_flight_total = convert_decimal(Decimal(str(flight.total_price)), flight.currency, target_currency)
                    if hotel_raw.get("total_stay_price") not in (None, ""):
                        base_hotel_total = _as_decimal(hotel_raw, "total_stay_price", Decimal(str(hotel.total_price)))
                    else:
                        nightly_exact = _as_decimal(
                            hotel_raw,
                            "nightly_price",
                            Decimal(str(hotel.total_price)) / max(selected_nights, 1),
                        )
                        base_hotel_total = _quantize(nightly_exact * Decimal(str(selected_nights)))
                    exact_hotel_total = convert_decimal(base_hotel_total, hotel.currency, target_currency)
                    package_total = _quantize(exact_flight_total + exact_hotel_total)

                    est_total_min = _quantize(est_flight_min + (est_hotel_min * nights_low))
                    est_total_max = _quantize(est_flight_max + (est_hotel_max * nights_high))
                    if est_total_min > package_total:
                        est_total_min = package_total
                    if est_total_max < package_total:
                        est_total_max = package_total

                    freshness_candidates = [value for value in [flight.last_checked_at, hotel.last_checked_at] if value]
                    freshness_candidates.extend([tour.last_checked_at for tour in selected_tours if tour.last_checked_at])
                    freshness_at = min(freshness_candidates) if freshness_candidates else timezone.now()

                    distance_band = str(
                        flight_raw.get("distance_band")
                        or hotel_raw.get("distance_band")
                        or (candidate.metadata or {}).get("distance_band")
                        or "medium"
                    )
                    nonstop_likelihood = float(
                        flight_raw.get("nonstop_likelihood")
                        or (candidate.metadata or {}).get("nonstop_likelihood")
                        or 0.55,
                    )
                    season_multiplier = float(
                        flight_raw.get("season_multiplier")
                        or hotel_raw.get("season_multiplier")
                        or 1.0,
                    )
                    source = str(flight_raw.get("data_source") or hotel_raw.get("data_source") or "fallback")

                    link_confidences = [_option_link_confidence(flight), _option_link_confidence(hotel)]
                    if selected_tours:
                        link_confidences.extend([_option_link_confidence(tour) for tour in selected_tours])
                    data_confidence = max(0.25, min(0.95, sum(link_confidences) / max(1, len(link_confidences))))

                    timezone_delta_hours = abs(
                        _timezone_offset_hours(candidate.timezone)
                        - _timezone_offset_hours(origin_tz),
                    )

                    score = score_package(
                        total_minor=to_minor_units(package_total),
                        budget_minor=budget_minor,
                        preference_weights=preferences,
                        candidate_tags=tags,
                        season_multiplier=season_multiplier,
                        distance_band=distance_band,
                        nonstop_likelihood=nonstop_likelihood,
                        freshness_at=freshness_at,
                        timezone_delta_hours=timezone_delta_hours,
                        travel_time_minutes=int(flight.duration_minutes or 0),
                        data_confidence=data_confidence,
                    )

                    family_friendly_score = round(
                        max(
                            0.0,
                            min(
                                100.0,
                                45.0
                                + (float(hotel.guest_rating or 0) * 4.0)
                                + (float(hotel.star_rating or 0) * 2.0)
                                - (int(flight.stops or 0) * 10.0)
                                + (8.0 if "family" in tags else 0.0),
                            ),
                        ),
                        2,
                    )

                    breakdown = score.breakdown.copy()
                    breakdown["distance_band"] = distance_band
                    breakdown["season_multiplier"] = season_multiplier
                    breakdown["freshness_timestamp"] = freshness_at.isoformat()
                    breakdown["source"] = source
                    breakdown["data_confidence"] = round(data_confidence, 2)
                    breakdown["family_friendly"] = family_friendly_score

                    combinations.append(
                        {
                            "candidate": candidate,
                            "flight": flight,
                            "hotel": hotel,
                            "selected_tours": selected_tours,
                            "exact_flight_total": _quantize(exact_flight_total),
                            "exact_hotel_total": _quantize(exact_hotel_total),
                            "tours_total": tours_total,
                            "optional_tours_total": optional_tours_total,
                            "tours_estimated": estimated_tours_used,
                            "package_total": package_total,
                            "estimated_flight_min": est_flight_min,
                            "estimated_flight_max": est_flight_max,
                            "estimated_hotel_nightly_min": est_hotel_min,
                            "estimated_hotel_nightly_max": est_hotel_max,
                            "estimated_total_min": est_total_min,
                            "estimated_total_max": est_total_max,
                            "freshness_at": freshness_at,
                            "score": score.score,
                            "price_score": score.price_score,
                            "convenience_score": score.convenience_score,
                            "quality_score": score.quality_score,
                            "location_score": score.location_score,
                            "family_friendly_score": family_friendly_score,
                            "explanations": score.explanations,
                            "score_breakdown": breakdown,
                            "data_confidence": data_confidence,
                        },
                    )

    if not combinations:
        return []

    combinations.sort(key=_sort_key(sort_mode))
    selected = _dedupe_sorted_combinations(combinations, max_packages=max_packages)

    created_packages: list[PackageOption] = []
    with transaction.atomic():
        for idx, item in enumerate(selected, start=1):
            candidate = item["candidate"]
            entity_payload = _candidate_entities_map(candidate)
            selected_flight_payload = _flight_component_payload(plan, candidate, item["flight"], target_currency)
            selected_hotel_payload = _hotel_component_payload(candidate, item["hotel"], target_currency)
            selected_tour_payloads = [
                _tour_component_payload(plan, candidate, tour, target_currency)
                for tour in item.get("selected_tours", [])
            ]

            default_flights, default_hotels, default_tours, default_places = _default_entities(plan, candidate, item, target_currency)
            flights_payload = _merge_selected_first(
                selected_flight_payload,
                entity_payload.get("flights") or default_flights,
                max_items=8,
            )
            hotels_payload = _merge_selected_first(
                selected_hotel_payload,
                entity_payload.get("hotels") or default_hotels,
                max_items=12,
            )
            if selected_tour_payloads:
                tours_payload = list(selected_tour_payloads)
                extra_tours = entity_payload.get("tours") or default_tours
                selected_tour_links = {
                    str(t.get("outbound_url") or t.get("link") or "")
                    for t in selected_tour_payloads
                }
                for raw in extra_tours:
                    if not isinstance(raw, dict):
                        continue
                    link = str(raw.get("outbound_url") or raw.get("link") or "")
                    if link and link in selected_tour_links:
                        continue
                    item_copy = dict(raw)
                    item_copy.setdefault("outbound_url", link)
                    item_copy.setdefault("link", link)
                    item_copy.setdefault("deeplink_url", link)
                    item_copy.setdefault("link_type", "search")
                    item_copy.setdefault("fallback_search", str(item_copy.get("link_type") or "search") != "item")
                    item_copy.setdefault("confidence", 0.45)
                    item_copy.setdefault("rationale", "Additional tour candidate metadata.")
                    tours_payload.append(item_copy)
                    if len(tours_payload) >= 8:
                        break
            else:
                tours_payload = entity_payload.get("tours") or default_tours
            places_payload = entity_payload.get("places") or default_places
            places_payload = _candidate_place_entities(candidate) or places_payload

            first_tour_link = ""
            if tours_payload:
                first_tour_link = str(tours_payload[0].get("outbound_url") or tours_payload[0].get("link") or "")

            breakdown_total = _quantize(item["exact_flight_total"] + item["exact_hotel_total"] + item["tours_total"])
            exact_total = breakdown_total
            if breakdown_total != _quantize(item["package_total"]):
                logger.warning(
                    "Package total corrected to strict component sum",
                    extra={
                        "plan_id": str(plan.id),
                        "candidate": candidate.airport_code,
                        "rank": idx,
                        "input_total": _decimal_str(item["package_total"]),
                        "strict_total": _decimal_str(breakdown_total),
                    },
                )
            price_breakdown = {
                "flight_total": _decimal_str(item["exact_flight_total"]),
                "hotel_total": _decimal_str(item["exact_hotel_total"]),
                "tours_total": "0.00",
                "optional_tours_total": _decimal_str(item.get("optional_tours_total") or Decimal("0.00")),
                "tours_estimated": False,
                "fees_variance": "0.00",
                "package_total": _decimal_str(exact_total),
                "currency": target_currency,
                "flight": {"amount": _decimal_str(item["exact_flight_total"]), "currency": target_currency},
                "hotel": {"amount": _decimal_str(item["exact_hotel_total"]), "currency": target_currency},
                "tours": {"amount": "0.00", "currency": target_currency},
                "optional_tours": {"amount": _decimal_str(item.get("optional_tours_total") or Decimal("0.00")), "currency": target_currency},
                "total": {"amount": _decimal_str(exact_total), "currency": target_currency},
            }

            component_links = {
                "flight": {
                    "outbound_url": selected_flight_payload["outbound_url"],
                    "link_type": selected_flight_payload["link_type"],
                    "fallback_search": bool(selected_flight_payload.get("fallback_search")),
                    "confidence": selected_flight_payload["confidence"],
                    "rationale": selected_flight_payload["rationale"],
                },
                "hotel": {
                    "outbound_url": selected_hotel_payload["outbound_url"],
                    "link_type": selected_hotel_payload["link_type"],
                    "fallback_search": bool(selected_hotel_payload.get("fallback_search")),
                    "confidence": selected_hotel_payload["confidence"],
                    "rationale": selected_hotel_payload["rationale"],
                },
                "tours": [
                    {
                        "outbound_url": tour["outbound_url"],
                        "link_type": tour["link_type"],
                        "fallback_search": bool(tour.get("fallback_search")),
                        "confidence": tour["confidence"],
                        "rationale": tour["rationale"],
                    }
                    for tour in selected_tour_payloads[:3]
                ],
            }

            why_ranked = list(item["explanations"])
            why_ranked.extend(
                [
                    f"Flight: {selected_flight_payload['link_type']} link ({selected_flight_payload['confidence']:.2f} confidence).",
                    f"Hotel: {selected_hotel_payload['link_type']} link ({selected_hotel_payload['confidence']:.2f} confidence).",
                ],
            )
            if selected_tour_payloads:
                why_ranked.append(f"{len(selected_tour_payloads)} optional tours attached (not included in package total).")
            else:
                why_ranked.append("No tours attached; package total includes only selected flight + hotel.")

            score_breakdown = dict(item["score_breakdown"])
            score_breakdown["why_ranked"] = why_ranked

            component_summary = {
                "flight": selected_flight_payload,
                "hotel": selected_hotel_payload,
                "tours": selected_tour_payloads[:3],
                "signals": {
                    "duration_minutes": int(item["flight"].duration_minutes or 0),
                    "stops": int(item["flight"].stops or 0),
                    "hotel_star_rating": float(item["hotel"].star_rating or 0),
                    "hotel_guest_rating": float(item["hotel"].guest_rating or 0),
                    "hotel_neighborhood": str(item["hotel"].neighborhood or ""),
                    "family_friendly_score": item["family_friendly_score"],
                },
            }

            package = PackageOption.objects.create(
                plan=plan,
                candidate=candidate,
                flight_option=item["flight"],
                hotel_option=item["hotel"],
                rank=idx,
                currency=target_currency,
                total_price=exact_total,
                amount_minor=to_minor_units(exact_total),
                estimated_total_min=item["estimated_total_min"],
                estimated_total_max=item["estimated_total_max"],
                estimated_flight_min=item["estimated_flight_min"],
                estimated_flight_max=item["estimated_flight_max"],
                estimated_hotel_nightly_min=item["estimated_hotel_nightly_min"],
                estimated_hotel_nightly_max=item["estimated_hotel_nightly_max"],
                freshness_at=item["freshness_at"],
                flight_url=selected_flight_payload["outbound_url"],
                hotel_url=selected_hotel_payload["outbound_url"],
                tours_url=first_tour_link
                or build_tour_search_link(
                    city=candidate.city_name,
                    country_code=candidate.country_code,
                    plan_id=str(plan.id),
                ),
                flight_entities=flights_payload,
                hotel_entities=hotels_payload,
                tour_entities=tours_payload,
                place_entities=places_payload,
                selected_tour_option_ids=[str(tour.id) for tour in item.get("selected_tours", [])[:3]],
                price_breakdown=price_breakdown,
                component_links=component_links,
                component_summary=component_summary,
                data_confidence=item["data_confidence"],
                score=item["score"],
                price_score=item["price_score"],
                convenience_score=item["convenience_score"],
                quality_score=item["quality_score"],
                location_score=item["location_score"],
                explanations=why_ranked,
                score_breakdown=score_breakdown,
                last_scored_at=timezone.now(),
            )
            if item.get("selected_tours"):
                package.tour_options.set(item["selected_tours"][:3])
            created_packages.append(package)

    return created_packages
