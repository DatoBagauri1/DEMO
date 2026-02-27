from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from urllib.parse import quote_plus

from django.core.cache import cache
from django.utils import timezone

from planner.services.http_client import build_http_client
from planner.services.unsplash import LOCAL_IMAGE_POOL

logger = logging.getLogger(__name__)

PLACES_CACHE_TTL = 60 * 60 * 6
PLACES_RETRYABLE_HTTP_STATUSES = {429, 500, 502, 503, 504}


@dataclass(frozen=True)
class PlacesFetchResult:
    places: list[dict]
    source: str
    partial: bool = False
    error: str = ""
    http_status: int | None = None


class PlacesFetchError(Exception):
    def __init__(self, message: str, *, http_status: int | None = None):
        super().__init__(message)
        self.http_status = http_status


def _fallback_places(city: str, country: str, limit: int) -> list[dict]:
    defaults = [
        "Old Town",
        "City Center",
        "National Museum",
        "Historic District",
        "Waterfront",
        "Main Cathedral",
        "Public Square",
        "City Park",
        "Landmark Tower",
        "Local Market",
        "Art Gallery",
        "Botanical Garden",
    ]
    payload = []
    for idx, label in enumerate(defaults[:limit], start=1):
        title = f"{city} {label}"
        payload.append(
            {
                "title": title,
                "name": title,
                "description": f"Popular stop in {city}, {country}.",
                "link": f"https://www.google.com/search?q={quote_plus(title)}",
                "image_url": LOCAL_IMAGE_POOL[(idx - 1) % len(LOCAL_IMAGE_POOL)],
                "provider": "fallback",
                "kind": "place",
            },
        )
    return payload


def _cache_payload(result: PlacesFetchResult) -> dict:
    return {
        "places": result.places,
        "source": result.source,
        "partial": bool(result.partial),
        "error": result.error or "",
        "http_status": result.http_status,
    }


def _result_from_cached(value) -> PlacesFetchResult | None:  # noqa: ANN001
    if value is None:
        return None
    if isinstance(value, list):
        return PlacesFetchResult(places=value, source="cache")
    if isinstance(value, dict) and isinstance(value.get("places"), list):
        return PlacesFetchResult(
            places=value["places"],
            source=str(value.get("source") or "cache"),
            partial=bool(value.get("partial")),
            error=str(value.get("error") or ""),
            http_status=value.get("http_status"),
        )
    return None


def _request_json_with_retry(client, url: str, params: dict) -> dict:  # noqa: ANN001
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            response = client.get(url, params=params)
            status_code = int(response.status_code)
            if status_code in PLACES_RETRYABLE_HTTP_STATUSES and attempt < 2:
                time.sleep(0.2 * (attempt + 1))
                continue
            if status_code >= 400:
                raise PlacesFetchError(f"HTTP {status_code} for Wikimedia API", http_status=status_code)
            return response.json()
        except PlacesFetchError:
            raise
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            if attempt < 2:
                time.sleep(0.2 * (attempt + 1))
                continue
    raise PlacesFetchError(f"Wikimedia transport error: {last_error}") from last_error


def fetch_places_result(*, city: str, country: str, latitude: float | None, longitude: float | None, limit: int = 10) -> PlacesFetchResult:
    if not city:
        return PlacesFetchResult(places=[], source="empty")
    cache_key = f"places:{city.lower()}:{country.lower()}:{latitude}:{longitude}:{limit}"
    cached = _result_from_cached(cache.get(cache_key))
    if cached is not None:
        return cached

    if latitude is None or longitude is None:
        payload = _fallback_places(city, country, limit)
        result = PlacesFetchResult(places=payload, source="fallback")
        cache.set(cache_key, _cache_payload(result), PLACES_CACHE_TTL)
        cache.set("places:last_success_at", timezone.now().isoformat(), timeout=60 * 60 * 24)
        return result

    try:
        geosearch_url = "https://en.wikipedia.org/w/api.php"
        geosearch_params = {
            "action": "query",
            "list": "geosearch",
            "gscoord": f"{latitude}|{longitude}",
            "gsradius": 12000,
            "gslimit": min(30, max(8, limit)),
            "format": "json",
        }
        with build_http_client(accept="application/json") as client:
            geodata = _request_json_with_retry(client, geosearch_url, geosearch_params)
            geo_items = geodata.get("query", {}).get("geosearch", [])
            if not geo_items:
                payload = _fallback_places(city, country, limit)
                result = PlacesFetchResult(places=payload, source="wikimedia-empty")
                cache.set(cache_key, _cache_payload(result), PLACES_CACHE_TTL)
                return result

            page_ids = [str(item.get("pageid")) for item in geo_items if item.get("pageid")][:limit]
            image_params = {
                "action": "query",
                "format": "json",
                "prop": "pageimages|info",
                "inprop": "url",
                "pageids": "|".join(page_ids),
                "pithumbsize": 900,
            }
            imagedata = _request_json_with_retry(client, geosearch_url, image_params)
            pages = imagedata.get("query", {}).get("pages", {})

            payload = []
            for item in geo_items[:limit]:
                pageid = str(item.get("pageid"))
                title = item.get("title")
                if not title:
                    continue
                page = pages.get(pageid, {})
                image_url = (page.get("thumbnail") or {}).get("source") or LOCAL_IMAGE_POOL[len(payload) % len(LOCAL_IMAGE_POOL)]
                link = page.get("fullurl") or f"https://en.wikipedia.org/wiki/{quote_plus(title.replace(' ', '_'))}"
                payload.append(
                    {
                        "title": title,
                        "name": title,
                        "description": f"Must-see place near {city}.",
                        "link": link,
                        "image_url": image_url,
                        "provider": "wikimedia",
                        "kind": "place",
                    },
                )

        if not payload:
            payload = _fallback_places(city, country, limit)
        result = PlacesFetchResult(places=payload, source="wikimedia")
        cache.set(cache_key, _cache_payload(result), PLACES_CACHE_TTL)
        cache.set("places:last_success_at", timezone.now().isoformat(), timeout=60 * 60 * 24)
        return result
    except PlacesFetchError as exc:
        logger.warning("Places fetch failed for %s: %s", city, exc)
        payload = _fallback_places(city, country, limit)
        result = PlacesFetchResult(
            places=payload,
            source="fallback",
            partial=True,
            error=str(exc),
            http_status=exc.http_status,
        )
        cache.set(cache_key, _cache_payload(result), 60 * 30)
        return result
    except Exception as exc:  # noqa: BLE001
        logger.warning("Places fetch failed for %s: %s", city, exc)
        payload = _fallback_places(city, country, limit)
        result = PlacesFetchResult(
            places=payload,
            source="fallback",
            partial=True,
            error=str(exc),
        )
        cache.set(cache_key, _cache_payload(result), 60 * 30)
        return result


def fetch_places(*, city: str, country: str, latitude: float | None, longitude: float | None, limit: int = 10) -> list[dict]:
    return fetch_places_result(
        city=city,
        country=country,
        latitude=latitude,
        longitude=longitude,
        limit=limit,
    ).places


def places_last_success_at() -> str | None:
    value = cache.get("places:last_success_at")
    if value:
        return str(value)
    return None
