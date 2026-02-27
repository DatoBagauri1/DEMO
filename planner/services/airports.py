from __future__ import annotations

from datetime import datetime
from functools import lru_cache

from django.core.cache import cache
from django.db.models import Case, IntegerField, Q, Value, When
from django.utils import timezone

from planner.models import Airport


AIRPORT_SEARCH_TTL = 60 * 15
AIRPORT_SEARCH_LIMIT = 12
AIRPORTS_METADATA_CACHE_KEY = "airports:dataset:metadata"


def normalize_iata(value: str) -> str:
    return (value or "").strip().upper()


def airport_display_name(airport: Airport) -> str:
    return f"{airport.iata} - {airport.name} ({airport.city}, {airport.country})"


def get_airport(iata: str) -> Airport | None:
    code = normalize_iata(iata)
    if len(code) != 3:
        return None
    return Airport.objects.filter(iata=code).first()


def airport_exists(iata: str) -> bool:
    return get_airport(iata) is not None


def airport_coordinates(iata: str) -> tuple[float, float] | None:
    airport = get_airport(iata)
    if not airport or airport.latitude is None or airport.longitude is None:
        return None
    return float(airport.latitude), float(airport.longitude)


def airport_timezone(iata: str) -> str:
    airport = get_airport(iata)
    return airport.timezone if airport else ""


def resolve_origin_code(origin_input: str) -> str:
    probe = normalize_iata(origin_input)
    if len(probe) >= 3 and probe[:3].isalpha():
        possible = probe[:3]
        if airport_exists(possible):
            return possible

    label = (origin_input or "").strip()
    if not label:
        return ""

    query = search_airports(label, limit=1)
    if query:
        return query[0]["iata"]
    return probe[:3]


def search_airports(query: str, limit: int = AIRPORT_SEARCH_LIMIT) -> list[dict[str, str]]:
    q = (query or "").strip()
    if len(q) < 1:
        return []
    q_upper = q.upper()
    cache_key = f"airports:search:{q_upper}:{max(1, int(limit))}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    queryset = (
        Airport.objects.filter(
            Q(iata__istartswith=q_upper)
            | Q(city__istartswith=q)
            | Q(name__istartswith=q)
            | Q(city__icontains=q)
            | Q(name__icontains=q)
        )
        .annotate(
            match_rank=Case(
                When(iata__iexact=q_upper, then=Value(0)),
                When(iata__istartswith=q_upper, then=Value(1)),
                When(city__istartswith=q, then=Value(2)),
                When(name__istartswith=q, then=Value(3)),
                default=Value(4),
                output_field=IntegerField(),
            ),
        )
        .order_by("match_rank", "iata", "city")[: max(1, min(limit, 25))]
    )
    payload = [
        {
            "iata": airport.iata,
            "display_name": airport_display_name(airport),
            "name": airport.name,
            "city": airport.city,
            "country": airport.country,
            "country_code": airport.country_code,
            "timezone": airport.timezone,
        }
        for airport in queryset
    ]
    cache.set(cache_key, payload, AIRPORT_SEARCH_TTL)
    return payload


@lru_cache(maxsize=1)
def top_airports() -> list[Airport]:
    return list(Airport.objects.order_by("iata")[:2500])


def refresh_airports_top_cache() -> None:
    top_airports.cache_clear()


def set_airports_dataset_metadata(loaded_count: int, loaded_at: datetime | None = None) -> None:
    cache.set(
        AIRPORTS_METADATA_CACHE_KEY,
        {
            "loaded_count": int(loaded_count),
            "loaded_at": (loaded_at or timezone.now()).isoformat(),
        },
        timeout=60 * 60 * 24 * 7,
    )


def airports_dataset_metadata() -> dict:
    payload = cache.get(AIRPORTS_METADATA_CACHE_KEY)
    if payload:
        return payload
    loaded_count = Airport.objects.count()
    latest = Airport.objects.order_by("-updated_at").values_list("updated_at", flat=True).first()
    payload = {
        "loaded_count": loaded_count,
        "loaded_at": latest.isoformat() if latest else None,
    }
    cache.set(AIRPORTS_METADATA_CACHE_KEY, payload, timeout=60 * 60 * 24)
    return payload
