from __future__ import annotations

import logging
import os
import time as time_module
from datetime import datetime, time
from decimal import Decimal
from urllib.parse import quote, urlparse

from celery import chord, shared_task
from django.db import OperationalError
from django.utils import timezone

from planner.models import (
    DestinationCandidate,
    FlightOption,
    HotelOption,
    PackageOption,
    PlanRequest,
    Profile,
    ProviderCall,
    ProviderError,
    TourOption,
)
from planner.services.airports import airport_coordinates, airport_timezone
from planner.services.deeplinks import resolve_partner_deeplink
from planner.services.entities import (
    build_flight_entities_for_candidate,
    build_hotel_entities_for_candidate,
    build_tour_entities_for_candidate,
)
from planner.services.destination_service import build_destination_candidates
from planner.services.fx import refresh_fx_rates, to_minor_units
from planner.services.package_builder import build_packages_for_plan
from planner.services.places import PlacesFetchResult, fetch_places_result
from planner.services.provider_registry import get_market_provider
from planner.services.providers.base import ProviderException
from planner.services.travelpayouts.fallbacks import tier_profile
from planner.services.travelpayouts.types import CandidateEstimate
from trip_pilot.logging import clear_request_context, set_request_context

logger = logging.getLogger(__name__)
CONCRETE_OFFER_UNSUPPORTED_ERROR = "Provider does not support item offers; cannot build concrete package"


def _is_search_result_url(url: str) -> bool:
    candidate = str(url or "").strip().lower()
    if not candidate:
        return True
    parsed = urlparse(candidate)
    path = parsed.path or ""
    query = parsed.query or ""
    return (
        "searchresults" in path
        or "/search" in path
        or "search=" in query
        or "q=" in query and "getyourguide.com" in candidate
    )


def _stable_id_from_item_url(url: str) -> str:
    parsed = urlparse(str(url or "").strip())
    path = str(parsed.path or "").strip("/")
    if not path:
        return ""
    token = path.split("/")[-1]
    if "." in token:
        token = token.split(".")[0]
    return token[:128]


def _extract_concrete_flight_offer(raw_payload: dict, destination_code: str) -> dict:
    endpoints = (raw_payload or {}).get("endpoints") or {}
    collected: list[dict] = []
    for endpoint_key in ("calendar", "city_directions", "cheap"):
        payload = endpoints.get(endpoint_key)
        if not isinstance(payload, dict):
            continue
        data = payload.get("data")
        if isinstance(data, dict):
            values = list(data.values())
        elif isinstance(data, list):
            values = list(data)
        else:
            values = []
        for item in values:
            if not isinstance(item, dict):
                continue
            destination = str(item.get("destination") or "").upper()
            if destination and destination != str(destination_code or "").upper():
                continue
            try:
                price = Decimal(str(item.get("price")))
            except Exception:  # noqa: BLE001
                continue
            if price <= 0:
                continue
            collected.append(item)
    if not collected:
        return {}
    collected.sort(key=lambda item: Decimal(str(item.get("price") or "0")))
    chosen = dict(collected[0])
    try:
        chosen["price"] = Decimal(str(chosen.get("price"))).quantize(Decimal("0.01"))
    except Exception:  # noqa: BLE001
        chosen["price"] = Decimal("0.00")
    return chosen


def _set_status(plan: PlanRequest, status: str, message: str, percent: int, *, error_message: str = "") -> None:
    updates = {
        "status": status,
        "progress_message": message,
        "progress_percent": percent,
        "error_message": error_message,
    }
    if status == PlanRequest.Status.VALIDATING and not plan.started_at:
        updates["started_at"] = timezone.now()
    if status in {PlanRequest.Status.COMPLETED, PlanRequest.Status.FAILED}:
        updates["completed_at"] = timezone.now()
    for attempt in range(3):
        try:
            PlanRequest.objects.filter(pk=plan.pk).update(**updates)
            for field, value in updates.items():
                setattr(plan, field, value)
            return
        except OperationalError as exc:
            if "database is locked" not in str(exc).lower() or attempt >= 2:
                raise
            time_module.sleep(0.05 * (attempt + 1))


def _places_error_type(result: PlacesFetchResult) -> str:
    if result.http_status == 429:
        return ProviderError.ErrorType.RATE_LIMIT
    if result.http_status == 403:
        return ProviderError.ErrorType.AUTH
    if "timeout" in (result.error or "").lower():
        return ProviderError.ErrorType.TIMEOUT
    return ProviderError.ErrorType.UNKNOWN


def _correlation_id(plan_id: str, provider: str, target: str) -> str:
    return f"{plan_id}:{provider}:{target}"[:64]


def _record_provider_call(
    *,
    provider: str,
    plan: PlanRequest | None,
    success: bool,
    error_type: str = ProviderError.ErrorType.UNKNOWN,
    http_status: int | None = None,
    latency_ms: int | None = None,
    correlation_id: str = "",
) -> None:
    ProviderCall.objects.create(
        provider=provider,
        plan=plan,
        success=success,
        error_type=error_type,
        http_status=http_status,
        latency_ms=latency_ms,
        correlation_id=correlation_id[:64],
    )


def _record_provider_error(
    *,
    plan: PlanRequest,
    provider: str,
    context: str,
    message: str,
    error_type: str = ProviderError.ErrorType.UNKNOWN,
    http_status: int | None = None,
    latency_ms: int | None = None,
    raw_payload: dict | None = None,
) -> None:
    ProviderError.objects.create(
        plan=plan,
        provider=provider,
        context=context,
        error_message=message,
        error_type=error_type,
        http_status=http_status,
        provider_latency_ms=latency_ms,
        raw_payload=raw_payload or {},
    )


def _refresh_plan_fx(plan: PlanRequest) -> None:
    target = (plan.search_currency or "USD").upper()
    currencies = set(plan.flight_options.values_list("currency", flat=True))
    currencies.update(plan.hotel_options.values_list("currency", flat=True))
    currencies.add(target)
    for base_currency in sorted(code.upper() for code in currencies if code):
        refresh_fx_rates(base_currency=base_currency, quote_currencies={target})


def _safe_datetime(day) -> datetime | None:  # noqa: ANN001
    if not day:
        return None
    return timezone.make_aware(datetime.combine(day, time(hour=9, minute=0)))


def _estimate_to_snapshot(estimate: CandidateEstimate) -> dict:
    return {
        "provider": estimate.provider,
        "source": estimate.source,
        "currency": estimate.currency,
        "flight_min": str(estimate.flight_min),
        "flight_max": str(estimate.flight_max),
        "hotel_nightly_min": str(estimate.hotel_nightly_min),
        "hotel_nightly_max": str(estimate.hotel_nightly_max),
        "freshness_at": estimate.freshness_at.isoformat(),
        "distance_km": estimate.distance_km,
        "distance_band": estimate.distance_band,
        "travel_time_minutes": estimate.travel_time_minutes,
        "nonstop_likelihood": estimate.nonstop_likelihood,
        "season_multiplier": estimate.season_multiplier,
        "tier": estimate.tier,
        "tags": estimate.tags,
        "raw_payload": estimate.raw_payload,
        "endpoints": estimate.endpoints,
        "error_type": estimate.error_type,
        "http_status": estimate.http_status,
        "error_summary": estimate.error_summary,
        "latency_ms": estimate.latency_ms,
    }


def _estimate_from_snapshot(snapshot: dict) -> CandidateEstimate:
    freshness_raw = snapshot.get("freshness_at")
    freshness_at = timezone.now()
    if freshness_raw:
        try:
            freshness_at = datetime.fromisoformat(str(freshness_raw).replace("Z", "+00:00"))
            if timezone.is_naive(freshness_at):
                freshness_at = timezone.make_aware(freshness_at)
        except Exception:  # noqa: BLE001
            freshness_at = timezone.now()

    return CandidateEstimate(
        provider=str(snapshot.get("provider") or "travelpayouts"),
        source=str(snapshot.get("source") or "fallback"),
        currency=str(snapshot.get("currency") or "USD"),
        flight_min=Decimal(str(snapshot.get("flight_min") or "0")),
        flight_max=Decimal(str(snapshot.get("flight_max") or "0")),
        hotel_nightly_min=Decimal(str(snapshot.get("hotel_nightly_min") or "0")),
        hotel_nightly_max=Decimal(str(snapshot.get("hotel_nightly_max") or "0")),
        freshness_at=freshness_at,
        distance_km=float(snapshot.get("distance_km") or 0),
        distance_band=str(snapshot.get("distance_band") or "medium"),
        travel_time_minutes=int(snapshot.get("travel_time_minutes") or 0),
        nonstop_likelihood=float(snapshot.get("nonstop_likelihood") or 0.55),
        season_multiplier=float(snapshot.get("season_multiplier") or 1.0),
        tier=str(snapshot.get("tier") or "standard"),
        tags=[str(item) for item in snapshot.get("tags") or []],
        raw_payload=dict(snapshot.get("raw_payload") or {}),
        endpoints=dict(snapshot.get("endpoints") or {}),
        error_type=snapshot.get("error_type"),
        http_status=snapshot.get("http_status"),
        error_summary=str(snapshot.get("error_summary") or ""),
        latency_ms=snapshot.get("latency_ms"),
    )


def _update_candidate_metadata(candidate: DestinationCandidate, updates: dict) -> None:
    metadata = dict(candidate.metadata or {})
    metadata.update(updates)
    candidate.metadata = metadata
    candidate.save(update_fields=["metadata", "updated_at"])


def _persist_candidate_options(
    *,
    plan: PlanRequest,
    candidate: DestinationCandidate,
    estimate: CandidateEstimate,
    depart_date,
    return_date,
) -> None:  # noqa: ANN001
    FlightOption.objects.filter(plan=plan, candidate=candidate).delete()
    HotelOption.objects.filter(plan=plan, candidate=candidate).delete()

    avg_nights = max(1, int((plan.trip_length_min + plan.trip_length_max) / 2))
    if depart_date and return_date and return_date > depart_date:
        selected_nights = max(1, int((return_date - depart_date).days))
    else:
        selected_nights = avg_nights
    destination_label = f"{candidate.city_name}-{candidate.country_code}"
    tier = tier_profile(estimate.tier)
    raw_estimate_payload = dict(estimate.raw_payload or {})
    concrete_flight_offer = _extract_concrete_flight_offer(raw_estimate_payload, candidate.airport_code)

    flight_item_url = str(
        raw_estimate_payload.get("flight_item_url")
        or raw_estimate_payload.get("flight_deeplink_url")
        or raw_estimate_payload.get("flight_offer_url")
        or "",
    ).strip()

    hotel_item_url = str(
        raw_estimate_payload.get("hotel_item_url")
        or raw_estimate_payload.get("hotel_deeplink_url")
        or raw_estimate_payload.get("hotel_offer_url")
        or "",
    ).strip()

    stable_flight_offer_id = str(
        raw_estimate_payload.get("flight_offer_id")
        or concrete_flight_offer.get("offer_id")
        or raw_estimate_payload.get("offer_id")
        or _stable_id_from_item_url(flight_item_url),
    )[:128]
    if not stable_flight_offer_id:
        raise RuntimeError(CONCRETE_OFFER_UNSUPPORTED_ERROR)

    if not flight_item_url:
        flight_item_url = f"https://www.aviasales.com/offer/{quote(stable_flight_offer_id, safe='')}"
    flight_deeplink, flight_link_type, flight_fallback_search = resolve_partner_deeplink(
        item_url=flight_item_url,
        search_url=flight_item_url,
        provider=estimate.provider,
        plan_id=str(plan.id),
        link_type="flight",
        destination=destination_label,
    )
    if flight_link_type != "item" or _is_search_result_url(flight_deeplink):
        raise RuntimeError(CONCRETE_OFFER_UNSUPPORTED_ERROR)

    stable_hotel_property_id = str(
        raw_estimate_payload.get("hotel_property_id")
        or raw_estimate_payload.get("provider_property_id")
        or _stable_id_from_item_url(hotel_item_url),
    )[:128]
    if not stable_hotel_property_id or not hotel_item_url:
        raise RuntimeError(CONCRETE_OFFER_UNSUPPORTED_ERROR)
    stable_hotel_offer_id = str(
        raw_estimate_payload.get("hotel_offer_id")
        or raw_estimate_payload.get("external_offer_id")
        or stable_hotel_property_id,
    )[:128]
    hotel_name = str(raw_estimate_payload.get("hotel_name") or f"{candidate.city_name} City Center Hotel").strip()[:255]
    hotel_deeplink, hotel_link_type, hotel_fallback_search = resolve_partner_deeplink(
        item_url=hotel_item_url,
        search_url=hotel_item_url,
        provider=estimate.provider,
        plan_id=str(plan.id),
        link_type="hotel",
        destination=destination_label,
    )
    if hotel_link_type != "item" or _is_search_result_url(hotel_deeplink):
        raise RuntimeError(CONCRETE_OFFER_UNSUPPORTED_ERROR)

    stops_by_band = {"short": 0, "medium": 1, "long": 1, "ultra_long": 2}
    base_flight_price = Decimal(str(concrete_flight_offer.get("price") or estimate.flight_mid)).quantize(Decimal("0.01"))
    if concrete_flight_offer:
        flight_total_price = (base_flight_price * Decimal(str(plan.total_travelers))).quantize(Decimal("0.01"))
    else:
        flight_total_price = base_flight_price
    flight_payload = {
        "estimated_min": str(estimate.flight_min),
        "estimated_max": str(estimate.flight_max),
        "distance_km": round(estimate.distance_km, 2),
        "distance_band": estimate.distance_band,
        "nonstop_likelihood": estimate.nonstop_likelihood,
        "season_multiplier": estimate.season_multiplier,
        "data_source": estimate.source,
        "provider_endpoints": estimate.endpoints,
        "link_type": flight_link_type,
        "fallback_search": flight_fallback_search,
        "link_confidence": 0.95,
        "link_rationale": "Concrete item-level flight offer deeplink.",
        "stable_offer_id": stable_flight_offer_id,
        "price_per_person": str(base_flight_price) if concrete_flight_offer else "",
        "price_for_travelers": str(flight_total_price),
        "traveler_count": plan.total_travelers,
        "airline": str(concrete_flight_offer.get("airline") or ""),
        "flight_number": str(concrete_flight_offer.get("flight_number") or ""),
        "departure_at": str(concrete_flight_offer.get("departure_at") or ""),
        "return_at": str(concrete_flight_offer.get("return_at") or ""),
    }
    flight_payload.update(raw_estimate_payload)

    hotel_payload = {
        "nightly_min": str(estimate.hotel_nightly_min),
        "nightly_max": str(estimate.hotel_nightly_max),
        "nightly_price": str(estimate.hotel_nightly_mid),
        "nights": selected_nights,
        "total_stay_price": str((estimate.hotel_nightly_mid * Decimal(str(selected_nights))).quantize(Decimal("0.01"))),
        "distance_km": round(estimate.distance_km, 2),
        "distance_band": estimate.distance_band,
        "nonstop_likelihood": estimate.nonstop_likelihood,
        "season_multiplier": estimate.season_multiplier,
        "data_source": estimate.source,
        "provider_endpoints": estimate.endpoints,
        "link_type": hotel_link_type,
        "fallback_search": hotel_fallback_search,
        "link_confidence": 0.9,
        "link_rationale": "Concrete item-level hotel property deeplink.",
        "provider_property_id": stable_hotel_property_id,
    }
    hotel_payload.update(raw_estimate_payload)

    flight = FlightOption.objects.create(
        plan=plan,
        candidate=candidate,
        provider=estimate.provider,
        external_offer_id=stable_flight_offer_id,
        origin_airport=plan.origin_code,
        destination_airport=candidate.airport_code,
        departure_at=_safe_datetime(depart_date),
        return_at=_safe_datetime(return_date),
        airline_codes=list(
            raw_estimate_payload.get("airline_codes")
            or ([str(concrete_flight_offer.get("airline")).upper()] if concrete_flight_offer.get("airline") else [])
        ),
        stops=int(concrete_flight_offer.get("transfers") or stops_by_band.get(estimate.distance_band, 1)),
        duration_minutes=max(90, int(estimate.travel_time_minutes)),
        currency=estimate.currency,
        total_price=flight_total_price,
        amount_minor=to_minor_units(flight_total_price),
        deeplink_url=flight_deeplink,
        link_type=flight_link_type,
        link_confidence=0.95 if flight_link_type == "item" else 0.88,
        link_rationale="Item-level flight offer deeplink.",
        raw_payload=flight_payload,
        last_checked_at=estimate.freshness_at,
    )

    hotel_total = (estimate.hotel_nightly_mid * Decimal(str(selected_nights))).quantize(Decimal("0.01"))

    HotelOption.objects.create(
        plan=plan,
        candidate=candidate,
        provider=estimate.provider,
        external_offer_id=stable_hotel_offer_id,
        provider_property_id=str(hotel_payload.get("provider_property_id") or ""),
        name=hotel_name,
        star_rating=float(tier.get("star_rating", 3.7)),
        guest_rating=float(tier.get("guest_rating", 8.0)),
        neighborhood="City center",
        latitude=candidate.latitude,
        longitude=candidate.longitude,
        amenities=(candidate.metadata or {}).get("tags", []),
        currency=estimate.currency,
        total_price=hotel_total,
        amount_minor=to_minor_units(hotel_total),
        deeplink_url=hotel_deeplink,
        link_type=hotel_link_type,
        link_confidence=0.9 if hotel_link_type == "item" else 0.58,
        link_rationale="Item-level hotel property deeplink.",
        raw_payload=hotel_payload,
        distance_km=estimate.distance_km,
        last_checked_at=estimate.freshness_at,
    )

    logger.info(
        "Candidate estimates persisted",
        extra={
            "plan_id": str(plan.id),
            "candidate": candidate.airport_code,
            "source": estimate.source,
            "flight_option": str(flight.id),
        },
    )


@shared_task(bind=True, soft_time_limit=90, time_limit=120)
def run_plan_pipeline(self, plan_id: str) -> None:  # noqa: ARG001
    set_request_context(plan_id=plan_id)
    try:
        plan = PlanRequest.objects.get(pk=plan_id)
        _set_status(plan, PlanRequest.Status.VALIDATING, "Validating airport request...", 8)

        explore = dict(plan.explore_constraints or {})
        explore["origin_timezone"] = airport_timezone(plan.origin_code)
        plan.explore_constraints = explore
        plan.save(update_fields=["explore_constraints", "updated_at"])

        _set_status(plan, PlanRequest.Status.EXPANDING_DESTINATIONS, "Expanding destination airports...", 16)
        max_items = int((plan.explore_constraints or {}).get("max_destinations") or 8)
        candidates = build_destination_candidates(plan, max_items=max(1, min(20, max_items)))
        if not candidates:
            _set_status(
                plan,
                PlanRequest.Status.FAILED,
                "No destination airports found for this request.",
                100,
                error_message="No destination candidates available.",
            )
            return

        _set_status(plan, PlanRequest.Status.FETCHING_FLIGHT_SIGNALS, "Fetching flight signals...", 30)
        jobs = [fetch_flight_signals_for_candidate.s(plan_id, candidate.id) for candidate in candidates]
        chord(jobs)(flights_stage_complete.s(plan_id))
    except Exception as exc:  # noqa: BLE001
        plan = PlanRequest.objects.filter(pk=plan_id).first()
        if plan:
            _set_status(
                plan,
                PlanRequest.Status.FAILED,
                "Plan pipeline failed.",
                100,
                error_message=str(exc)[:500],
            )
        raise
    finally:
        clear_request_context()


@shared_task(bind=True, soft_time_limit=90, time_limit=120)
def fetch_flight_signals_for_candidate(self, plan_id: str, candidate_id: int) -> dict:  # noqa: ARG001
    set_request_context(plan_id=plan_id)
    try:
        plan = PlanRequest.objects.get(pk=plan_id)
        candidate = plan.destination_candidates.get(pk=candidate_id)
        provider = get_market_provider()
        correlation_id = _correlation_id(plan_id, "travelpayouts", candidate.airport_code)

        depart_date, return_date = plan.resolve_dates()
        origin_coords = airport_coordinates(plan.origin_code)
        destination_coords = None
        if candidate.latitude is not None and candidate.longitude is not None:
            destination_coords = (float(candidate.latitude), float(candidate.longitude))
        else:
            destination_coords = airport_coordinates(candidate.airport_code)

        metadata = candidate.metadata or {}
        estimate = provider.estimate(
            origin_code=plan.origin_code,
            destination_code=candidate.airport_code,
            destination_city=candidate.city_name,
            destination_country=candidate.country_code,
            depart_date=depart_date,
            return_date=return_date,
            travelers=plan.total_travelers,
            tier=str(metadata.get("tier") or "standard"),
            tags=[str(tag).lower() for tag in metadata.get("tags", [])],
            origin_coords=origin_coords,
            destination_coords=destination_coords,
            nonstop_likelihood=metadata.get("nonstop_likelihood"),
            preferred_currency=plan.search_currency,
        )

        _persist_candidate_options(
            plan=plan,
            candidate=candidate,
            estimate=estimate,
            depart_date=depart_date,
            return_date=return_date,
        )
        flights_entities = build_flight_entities_for_candidate(
            plan=plan,
            candidate=candidate,
            estimate=estimate,
            depart_date=depart_date,
            return_date=return_date,
            count=8,
        )
        _update_candidate_metadata(
            candidate,
            {
                "estimate_snapshot": _estimate_to_snapshot(estimate),
                "entities": {
                    **((candidate.metadata or {}).get("entities") or {}),
                    "flights": flights_entities,
                },
                "signal_source": estimate.source,
                "signal_freshness_at": estimate.freshness_at.isoformat(),
            },
        )

        success = estimate.source == "travelpayouts"
        error_type = estimate.error_type or ProviderError.ErrorType.EMPTY
        if success:
            error_type = ProviderError.ErrorType.UNKNOWN

        _record_provider_call(
            provider="travelpayouts",
            plan=plan,
            success=success,
            error_type=error_type,
            http_status=estimate.http_status,
            latency_ms=estimate.latency_ms,
            correlation_id=correlation_id,
        )

        if estimate.error_summary:
            _record_provider_error(
                plan=plan,
                provider="travelpayouts",
                context=f"candidate={candidate.airport_code}",
                message=estimate.error_summary,
                error_type=estimate.error_type or ProviderError.ErrorType.UNKNOWN,
                http_status=estimate.http_status,
                latency_ms=estimate.latency_ms,
                raw_payload={"endpoints": estimate.endpoints},
            )

        return {
            "candidate_id": candidate_id,
            "ok": True,
            "source": estimate.source,
            "freshness": estimate.freshness_at.isoformat(),
        }
    except (PlanRequest.DoesNotExist, DestinationCandidate.DoesNotExist):
        return {"candidate_id": candidate_id, "ok": False, "error": "missing"}
    except ProviderException as exc:
        plan = PlanRequest.objects.filter(pk=plan_id).first()
        if plan:
            _record_provider_error(
                plan=plan,
                provider="travelpayouts",
                context=f"candidate={candidate_id}",
                message=str(exc),
                error_type=exc.error_type,
                http_status=exc.http_status,
                latency_ms=exc.latency_ms,
                raw_payload=exc.raw_payload,
            )
            _record_provider_call(
                provider="travelpayouts",
                plan=plan,
                success=False,
                error_type=exc.error_type,
                http_status=exc.http_status,
                latency_ms=exc.latency_ms,
                correlation_id=_correlation_id(plan_id, "travelpayouts", str(candidate_id)),
            )
        if exc.error_type in {ProviderError.ErrorType.TIMEOUT, ProviderError.ErrorType.RATE_LIMIT} and self.request.retries < 2:
            raise self.retry(exc=exc, countdown=2 ** self.request.retries)
        return {"candidate_id": candidate_id, "ok": False, "error": exc.error_type}
    except Exception as exc:  # noqa: BLE001
        plan = PlanRequest.objects.filter(pk=plan_id).first()
        message = str(exc) or "unexpected"
        if plan:
            _record_provider_error(
                plan=plan,
                provider="travelpayouts",
                context=f"candidate={candidate_id}",
                message=f"Unexpected flight-signal error: {message}",
                error_type=ProviderError.ErrorType.UNKNOWN,
            )
            _record_provider_call(
                provider="travelpayouts",
                plan=plan,
                success=False,
                error_type=ProviderError.ErrorType.UNKNOWN,
                correlation_id=_correlation_id(plan_id, "travelpayouts", str(candidate_id)),
            )
        return {"candidate_id": candidate_id, "ok": False, "error": message[:240]}
    finally:
        clear_request_context()


@shared_task(bind=True, soft_time_limit=90, time_limit=120)
def fetch_hotel_signals_for_candidate(self, plan_id: str, candidate_id: int) -> dict:  # noqa: ARG001
    set_request_context(plan_id=plan_id)
    try:
        plan = PlanRequest.objects.get(pk=plan_id)
        candidate = plan.destination_candidates.get(pk=candidate_id)
        snapshot = (candidate.metadata or {}).get("estimate_snapshot")
        if not snapshot:
            return {"candidate_id": candidate_id, "ok": False, "error": "missing_estimate"}
        estimate = _estimate_from_snapshot(snapshot)
        depart_date, return_date = plan.resolve_dates()

        hotel_entities = build_hotel_entities_for_candidate(
            plan=plan,
            candidate=candidate,
            estimate=estimate,
            depart_date=depart_date,
            return_date=return_date,
            count=12,
        )
        entities = dict((candidate.metadata or {}).get("entities") or {})
        entities["hotels"] = hotel_entities
        _update_candidate_metadata(candidate, {"entities": entities})
        return {"candidate_id": candidate_id, "ok": True}
    except Exception as exc:  # noqa: BLE001
        return {"candidate_id": candidate_id, "ok": False, "error": str(exc)[:120]}
    finally:
        clear_request_context()


@shared_task(bind=True, soft_time_limit=90, time_limit=120)
def fetch_tours_for_candidate(self, plan_id: str, candidate_id: int) -> dict:  # noqa: ARG001
    set_request_context(plan_id=plan_id)
    try:
        plan = PlanRequest.objects.get(pk=plan_id)
        candidate = plan.destination_candidates.get(pk=candidate_id)
        tours = build_tour_entities_for_candidate(plan=plan, candidate=candidate, count=8)
        TourOption.objects.filter(plan=plan, candidate=candidate).delete()
        tour_rows: list[TourOption] = []
        now = timezone.now()
        for idx, item in enumerate(tours[:8], start=1):
            if not isinstance(item, dict):
                continue
            deeplink = str(item.get("link") or item.get("outbound_url") or "").strip()
            title = str(item.get("title") or item.get("name") or f"{candidate.city_name} tours {idx}").strip()
            stable_id = str(item.get("stable_id") or f"search:{candidate.airport_code}:{idx}")
            raw_price = item.get("price")
            amount = Decimal("0")
            if raw_price not in (None, ""):
                try:
                    amount = Decimal(str(raw_price)).quantize(Decimal("0.01"))
                except Exception:  # noqa: BLE001
                    amount = Decimal("0")
            tour_rows.append(
                TourOption(
                    plan=plan,
                    candidate=candidate,
                    provider=str(item.get("provider") or "travelpayouts"),
                    external_product_id=stable_id[:128],
                    name=title[:255],
                    currency=str(item.get("currency") or ""),
                    total_price=amount,
                    amount_minor=to_minor_units(amount) if amount else 0,
                    deeplink_url=deeplink,
                    link_type=str(item.get("link_type") or "search")[:16],
                    link_confidence=float(item.get("confidence") or 0.45),
                    link_rationale=str(item.get("rationale") or "Search-link fallback; product-level tour offers unavailable in current provider path.")[:255],
                    raw_payload={
                        "description": str(item.get("description") or ""),
                        "image_url": str(item.get("image_url") or ""),
                        "kind": str(item.get("kind") or "tour"),
                        "outbound_url": deeplink,
                        "fallback_search": bool(item.get("fallback_search")) if "fallback_search" in item else str(item.get("link_type") or "search") != "item",
                    },
                    last_checked_at=now,
                ),
            )
        if tour_rows:
            TourOption.objects.bulk_create(tour_rows)
        entities = dict((candidate.metadata or {}).get("entities") or {})
        entities["tours"] = tours
        _update_candidate_metadata(candidate, {"entities": entities})
        return {"candidate_id": candidate_id, "ok": True}
    except Exception as exc:  # noqa: BLE001
        return {"candidate_id": candidate_id, "ok": False, "error": str(exc)[:120]}
    finally:
        clear_request_context()


@shared_task(bind=True, soft_time_limit=90, time_limit=120)
def fetch_places_for_candidate(self, plan_id: str, candidate_id: int) -> dict:  # noqa: ARG001
    set_request_context(plan_id=plan_id)
    try:
        plan = PlanRequest.objects.get(pk=plan_id)
        candidate = plan.destination_candidates.get(pk=candidate_id)
        places_result = fetch_places_result(
            city=candidate.city_name,
            country=candidate.country_code,
            latitude=candidate.latitude,
            longitude=candidate.longitude,
            limit=12,
        )
        places = places_result.places
        entities = dict((candidate.metadata or {}).get("entities") or {})
        entities["places"] = places
        metadata_update = {
            "entities": entities,
            "places_source": places_result.source,
            "places_partial": bool(places_result.partial),
            "places_error": (places_result.error or "")[:240],
        }
        _update_candidate_metadata(candidate, metadata_update)

        error_type = ProviderError.ErrorType.UNKNOWN if not places_result.partial else _places_error_type(places_result)

        _record_provider_call(
            provider="places",
            plan=plan,
            success=bool(places) and not places_result.partial,
            error_type=error_type,
            http_status=places_result.http_status,
            correlation_id=_correlation_id(plan_id, "places", candidate.airport_code),
        )

        if places_result.partial:
            _record_provider_error(
                plan=plan,
                provider="places",
                context=f"candidate={candidate.airport_code}",
                message=f"Places fallback used: {places_result.error or 'external source unavailable'}",
                error_type=error_type,
                http_status=places_result.http_status,
                raw_payload={"source": places_result.source},
            )

        return {
            "candidate_id": candidate_id,
            "ok": not places_result.partial,
            "partial": bool(places_result.partial),
            "source": places_result.source,
            "error": (places_result.error or "")[:120],
        }
    except Exception as exc:  # noqa: BLE001
        plan = PlanRequest.objects.filter(pk=plan_id).first()
        if plan:
            _record_provider_call(
                provider="places",
                plan=plan,
                success=False,
                error_type=ProviderError.ErrorType.UNKNOWN,
                correlation_id=_correlation_id(plan_id, "places", str(candidate_id)),
            )
            _record_provider_error(
                plan=plan,
                provider="places",
                context=f"candidate={candidate_id}",
                message=f"Places signal failed: {exc}",
                error_type=ProviderError.ErrorType.UNKNOWN,
            )
        return {"candidate_id": candidate_id, "ok": False, "error": str(exc)[:120]}
    finally:
        clear_request_context()


@shared_task(bind=True, soft_time_limit=90, time_limit=120)
def flights_stage_complete(self, results: list[dict], plan_id: str) -> None:  # noqa: ARG001
    set_request_context(plan_id=plan_id)
    try:
        plan = PlanRequest.objects.get(pk=plan_id)
        for item in results or []:
            if CONCRETE_OFFER_UNSUPPORTED_ERROR in str((item or {}).get("error") or ""):
                _set_status(
                    plan,
                    PlanRequest.Status.FAILED,
                    CONCRETE_OFFER_UNSUPPORTED_ERROR,
                    100,
                    error_message=CONCRETE_OFFER_UNSUPPORTED_ERROR,
                )
                return
        _set_status(plan, PlanRequest.Status.FETCHING_HOTEL_SIGNALS, "Fetching hotel signals...", 46)
        candidate_ids = list(plan.destination_candidates.values_list("id", flat=True))
        chord([fetch_hotel_signals_for_candidate.s(plan_id, cid) for cid in candidate_ids])(hotels_stage_complete.s(plan_id))
    finally:
        clear_request_context()


@shared_task(bind=True, soft_time_limit=90, time_limit=120)
def hotels_stage_complete(self, results: list[dict], plan_id: str) -> None:  # noqa: ARG001
    set_request_context(plan_id=plan_id)
    try:
        plan = PlanRequest.objects.get(pk=plan_id)
        _set_status(plan, PlanRequest.Status.FETCHING_TOURS, "Fetching tours...", 62)
        candidate_ids = list(plan.destination_candidates.values_list("id", flat=True))
        chord([fetch_tours_for_candidate.s(plan_id, cid) for cid in candidate_ids])(tours_stage_complete.s(plan_id))
    finally:
        clear_request_context()


@shared_task(bind=True, soft_time_limit=90, time_limit=120)
def tours_stage_complete(self, results: list[dict], plan_id: str) -> None:  # noqa: ARG001
    set_request_context(plan_id=plan_id)
    try:
        plan = PlanRequest.objects.get(pk=plan_id)
        _set_status(plan, PlanRequest.Status.FETCHING_PLACES, "Fetching must-see places...", 76)
        candidate_ids = list(plan.destination_candidates.values_list("id", flat=True))
        chord([fetch_places_for_candidate.s(plan_id, cid) for cid in candidate_ids])(places_stage_complete.s(plan_id))
    finally:
        clear_request_context()


@shared_task(bind=True, soft_time_limit=90, time_limit=120)
def places_stage_complete(self, results: list[dict], plan_id: str) -> None:  # noqa: ARG001
    set_request_context(plan_id=plan_id)
    try:
        plan = PlanRequest.objects.get(pk=plan_id)
        results = results or []
        total = len(results)
        failed = [item for item in results if not bool((item or {}).get("ok"))]
        message = "Scoring packages..."
        if total and failed:
            message = f"Scoring packages... (places partial for {len(failed)}/{total} destinations)"
        _set_status(plan, PlanRequest.Status.SCORING, message, 88)
        build_packages_task.delay(plan_id)
    finally:
        clear_request_context()


@shared_task(bind=True, soft_time_limit=120, time_limit=180)
def build_packages_task(self, plan_id: str) -> None:  # noqa: ARG001
    set_request_context(plan_id=plan_id)
    try:
        plan = PlanRequest.objects.get(pk=plan_id)
        if plan.flight_options.filter(link_type__iexact="search").exists() or plan.hotel_options.filter(link_type__iexact="search").exists():
            _set_status(
                plan,
                PlanRequest.Status.FAILED,
                CONCRETE_OFFER_UNSUPPORTED_ERROR,
                100,
                error_message=CONCRETE_OFFER_UNSUPPORTED_ERROR,
            )
            return
        if any(_is_search_result_url(url) for url in plan.flight_options.values_list("deeplink_url", flat=True)):
            _set_status(
                plan,
                PlanRequest.Status.FAILED,
                CONCRETE_OFFER_UNSUPPORTED_ERROR,
                100,
                error_message=CONCRETE_OFFER_UNSUPPORTED_ERROR,
            )
            return
        if any(_is_search_result_url(url) for url in plan.hotel_options.values_list("deeplink_url", flat=True)):
            _set_status(
                plan,
                PlanRequest.Status.FAILED,
                CONCRETE_OFFER_UNSUPPORTED_ERROR,
                100,
                error_message=CONCRETE_OFFER_UNSUPPORTED_ERROR,
            )
            return
        _set_status(plan, PlanRequest.Status.SCORING, "Normalizing FX and ranking package options...", 90)
        _refresh_plan_fx(plan)
        build_packages_for_plan(plan, sort_mode="best_value", max_packages=1, flights_per_city=1, hotels_per_city=1)
        package_count = plan.package_options.count()
        if package_count == 0:
            _set_status(
                plan,
                PlanRequest.Status.FAILED,
                "No package estimates returned for this search.",
                100,
                error_message="No package combinations available.",
            )
            return
        _set_status(plan, PlanRequest.Status.COMPLETED, f"Found {package_count} ranked links-only packages.", 100)
    except Exception as exc:  # noqa: BLE001
        plan = PlanRequest.objects.filter(pk=plan_id).first()
        if plan:
            _set_status(plan, PlanRequest.Status.FAILED, "Package build failed.", 100, error_message=str(exc))
        raise
    finally:
        clear_request_context()


@shared_task(bind=True, soft_time_limit=120, time_limit=180)
def refresh_top_packages_task(self, plan_id: str, limit: int = 5) -> int:  # noqa: ARG001
    set_request_context(plan_id=plan_id)
    try:
        plan = PlanRequest.objects.get(pk=plan_id)
        _set_status(plan, PlanRequest.Status.QUEUED, "Refresh queued.", 6)
        run_plan_pipeline.delay(plan_id)
        return int(limit or 5)
    finally:
        clear_request_context()


# Backward-compatible task names retained for monitoring/ops scripts.
@shared_task(bind=True, soft_time_limit=90, time_limit=120)
def fetch_candidate_market_data(self, plan_id: str, candidate_id: int) -> dict:  # noqa: ARG001
    return fetch_flight_signals_for_candidate(plan_id, candidate_id)


@shared_task(bind=True, soft_time_limit=90, time_limit=120)
def fetch_flights_for_candidate(self, plan_id: str, candidate_id: int) -> dict:  # noqa: ARG001
    return fetch_flight_signals_for_candidate(plan_id, candidate_id)


@shared_task(bind=True, soft_time_limit=90, time_limit=120)
def fetch_hotels_for_candidate(self, plan_id: str, candidate_id: int) -> dict:  # noqa: ARG001
    return fetch_hotel_signals_for_candidate(plan_id, candidate_id)


@shared_task(bind=True, soft_time_limit=90, time_limit=120)
def market_data_stage_complete(self, results: list[dict], plan_id: str) -> None:  # noqa: ARG001
    return flights_stage_complete(results, plan_id)


@shared_task(bind=True, soft_time_limit=90, time_limit=120)
def flights_stage_complete_legacy(self, results: list[dict], plan_id: str) -> None:  # noqa: ARG001
    return flights_stage_complete(results, plan_id)


@shared_task(bind=True, soft_time_limit=90, time_limit=120)
def hotels_stage_complete_legacy(self, results: list[dict], plan_id: str) -> None:  # noqa: ARG001
    return hotels_stage_complete(results, plan_id)


@shared_task(bind=True, autoretry_for=(Exception,), retry_backoff=True, retry_jitter=True, retry_kwargs={"max_retries": 3})
def refresh_fx_rates_daily(self) -> int:  # noqa: ARG001
    quote_currencies = set(
        code.upper()
        for code in Profile.objects.exclude(preferred_currency="").values_list("preferred_currency", flat=True)
    )
    quote_currencies.update(
        code.upper()
        for code in PlanRequest.objects.exclude(search_currency="").values_list("search_currency", flat=True)
    )
    from_env = [code.strip().upper() for code in os.getenv("FX_QUOTE_CURRENCIES", "USD,EUR,GBP,CAD").split(",") if code.strip()]
    quote_currencies.update(from_env)

    count = refresh_fx_rates(base_currency="USD", quote_currencies=quote_currencies)
    _record_provider_call(provider="fx", plan=None, success=True, error_type=ProviderError.ErrorType.UNKNOWN)
    return count


@shared_task
def cleanup_old_plans(days: int = 21) -> int:
    cutoff = timezone.now() - timezone.timedelta(days=days)
    stale = PlanRequest.objects.filter(
        status__in=[PlanRequest.Status.COMPLETED, PlanRequest.Status.FAILED],
        created_at__lt=cutoff,
    )
    count = stale.count()
    stale.delete()
    return count
