import logging
import time
from contextlib import nullcontext
from decimal import Decimal
from threading import Lock
from typing import Any

from django.db import IntegrityError, OperationalError, connections, transaction

from planner.models import PlanRequest
from planner.services.airports import get_airport, normalize_iata
from planner.services.config import default_origin_iata
from planner.services.destination_service import resolve_origin_code

logger = logging.getLogger(__name__)
_SQLITE_PLAN_CREATE_LOCK = Lock()
_REMOVED_FLIGHT_FILTER_KEYS = {"cabin", "cabin_class", "departure_type", "travel_class"}


def _sanitize_flight_filters(raw):  # noqa: ANN001, ANN201
    if not isinstance(raw, dict):
        return {}
    payload = dict(raw)
    for key in _REMOVED_FLIGHT_FILTER_KEYS:
        payload.pop(key, None)
    return payload


def _enqueue_plan_pipeline(plan_id: str) -> None:
    from planner.tasks import run_plan_pipeline

    try:
        run_plan_pipeline.delay(plan_id)
    except Exception:  # noqa: BLE001
        logger.exception("Failed to enqueue pipeline task", extra={"plan_id": plan_id})
        PlanRequest.objects.filter(pk=plan_id).update(
            status=PlanRequest.Status.FAILED,
            progress_message="Unable to queue planning job.",
            progress_percent=100,
            error_message="Task queue unavailable. Start Celery worker and broker, then retry.",
        )


def _plan_create_lock_context():
    if connections["default"].vendor == "sqlite":
        return _SQLITE_PLAN_CREATE_LOCK
    return nullcontext()


def create_plan_request(user, payload: dict[str, Any], *, idempotency_key: str | None = None) -> PlanRequest:  # noqa: ANN001
    fallback_origin = normalize_iata(default_origin_iata())
    raw_origin = payload.get("origin_iata") or payload.get("origin_input") or fallback_origin
    origin_input = str(raw_origin or "").strip().upper()
    origin_code = resolve_origin_code(origin_input)
    if not origin_code:
        raise ValueError("Origin airport is required.")

    search_mode = payload.get("search_mode") or PlanRequest.SearchMode.DIRECT
    destination_iata = normalize_iata(payload.get("destination_iata") or "")
    destination_iatas = [normalize_iata(code) for code in payload.get("destination_iatas", []) if normalize_iata(code)]

    if destination_iata and destination_iata not in destination_iatas:
        destination_iatas.insert(0, destination_iata)
    if not destination_iata and destination_iatas:
        destination_iata = destination_iatas[0]

    inferred_country = payload.get("destination_country") or ""
    if destination_iata:
        airport = get_airport(destination_iata)
        if airport and airport.country_code:
            inferred_country = airport.country_code
    destination_country = str(inferred_country or "XX").upper()[:2]

    trip_length_min = int(payload.get("trip_length_min") or payload.get("nights_min") or 3)
    trip_length_max = int(payload.get("trip_length_max") or payload.get("nights_max") or max(trip_length_min, 7))
    trip_length_max = max(trip_length_min, trip_length_max)

    adults = int(payload.get("adults") or payload.get("travelers") or 1)
    children = int(payload.get("children") or 0)
    total_travelers = max(1, adults + children)

    normalized_key = str(idempotency_key or payload.get("idempotency_key") or "").strip()[:64] or None
    is_authenticated_user = bool(getattr(user, "is_authenticated", False))
    user_obj = user if is_authenticated_user else None

    defaults: dict[str, Any] = {
        "user": user_obj,
        "origin_input": origin_input,
        "origin_code": origin_code,
        "origin_iata": origin_code,
        "search_mode": search_mode,
        "destination_input": str(payload.get("destination_input") or destination_iata),
        "destination_iata": destination_iata,
        "destination_iatas": destination_iatas,
        "destination_country": destination_country,
        "date_mode": payload.get("date_mode") or PlanRequest.DateMode.EXACT,
        "depart_date": payload.get("depart_date"),
        "return_date": payload.get("return_date"),
        "travel_month": payload.get("travel_month"),
        "departure_date_from": payload.get("departure_date_from"),
        "departure_date_to": payload.get("departure_date_to"),
        "flexibility_days": payload.get("flexibility_days", 0),
        "trip_length_min": trip_length_min,
        "trip_length_max": trip_length_max,
        "nights_min": trip_length_min,
        "nights_max": trip_length_max,
        "total_budget": payload.get("total_budget", Decimal("0.00")),
        "travelers": total_travelers,
        "adults": max(1, adults),
        "children": max(0, children),
        "search_currency": payload.get("search_currency", "USD"),
        "hotel_filters": payload.get("hotel_filters", {}),
        "flight_filters": _sanitize_flight_filters(payload.get("flight_filters", {})),
        "preference_weights": payload.get("preferences", {}),
        "explore_constraints": payload.get("explore_constraints", {}),
        "status": PlanRequest.Status.QUEUED,
        "progress_message": "Queued for airport-to-airport planning",
        "progress_percent": 5,
        "idempotency_key": normalized_key,
    }

    created = False
    last_error: OperationalError | None = None
    for attempt in range(4):
        try:
            with _plan_create_lock_context():
                with transaction.atomic():
                    if normalized_key and user_obj:
                        lookup = {"user": user_obj, "idempotency_key": normalized_key}
                        try:
                            plan, created = PlanRequest.objects.get_or_create(**lookup, defaults=defaults)
                        except IntegrityError:
                            plan = PlanRequest.objects.get(**lookup)
                            created = False
                    else:
                        plan = PlanRequest.objects.create(**defaults)
                        created = True

                    if created:
                        transaction.on_commit(lambda: _enqueue_plan_pipeline(str(plan.id)))
            break
        except OperationalError as exc:
            if "database is locked" not in str(exc).lower():
                raise
            last_error = exc
            if attempt >= 3:
                raise
            time.sleep(0.1 * (attempt + 1))
    else:
        if last_error:
            raise last_error

    if created:
        logger.info("Plan request created", extra={"plan_id": str(plan.id)})
    else:
        logger.info(
            "Plan request reused via idempotency key",
            extra={"plan_id": str(plan.id), "idempotency_key": normalized_key or ""},
        )
    return plan
