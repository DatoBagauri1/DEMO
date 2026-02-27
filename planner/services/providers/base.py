import hashlib
import json
import time
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import httpx
import isodate
from django.core.cache import cache
from planner.services.http_client import trippilot_user_agent


class ProviderException(Exception):
    def __init__(
        self,
        message: str,
        *,
        error_type: str = "unknown",
        http_status: int | None = None,
        latency_ms: int | None = None,
        raw_payload: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.error_type = error_type
        self.http_status = http_status
        self.latency_ms = latency_ms
        self.raw_payload = raw_payload or {}


@dataclass
class FlightSearchQuery:
    origin: str
    destination: str
    depart_date: date
    return_date: date | None
    travelers: int
    currency: str
    cabin: str = "economy"
    max_stops: int | None = None
    max_duration_minutes: int | None = None
    flexibility_days: int = 0

    def cache_payload(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["depart_date"] = self.depart_date.isoformat()
        payload["return_date"] = self.return_date.isoformat() if self.return_date else None
        return payload


@dataclass
class HotelSearchQuery:
    city_name: str
    country_code: str
    checkin: date
    checkout: date
    adults: int
    currency: str
    stars_min: int | None = None
    guest_rating_min: float | None = None
    amenities: list[str] | None = None
    budget_max: Decimal | None = None

    def cache_payload(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["checkin"] = self.checkin.isoformat()
        payload["checkout"] = self.checkout.isoformat()
        payload["budget_max"] = str(self.budget_max) if self.budget_max is not None else None
        return payload


@dataclass
class NormalizedFlightOption:
    provider: str
    external_offer_id: str
    origin_airport: str
    destination_airport: str
    departure_at: datetime | None
    return_at: datetime | None
    airline_codes: list[str]
    stops: int
    duration_minutes: int
    cabin_class: str
    currency: str
    total_price: Decimal
    deeplink_url: str
    raw_payload: dict[str, Any]


@dataclass
class NormalizedHotelOption:
    provider: str
    external_offer_id: str
    name: str
    star_rating: float
    guest_rating: float
    neighborhood: str
    latitude: float | None
    longitude: float | None
    amenities: list[str]
    currency: str
    total_price: Decimal
    deeplink_url: str
    raw_payload: dict[str, Any]


class ProviderMixin:
    timeout_seconds = 18
    max_retries = 3

    def _request_json(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        def classify_http_status(status_code: int | None) -> str:
            if status_code == 429:
                return "rate_limit"
            if status_code in {401, 403}:
                return "auth"
            if status_code in {402}:
                return "quota"
            if status_code and status_code >= 500:
                return "unknown"
            return "unknown"

        for attempt in range(1, self.max_retries + 1):
            started = time.monotonic()
            try:
                response = httpx.request(
                    method=method,
                    url=url,
                    headers={
                        "User-Agent": trippilot_user_agent(),
                        **(headers or {}),
                    },
                    params=params,
                    json=json_body,
                    data=data,
                    timeout=self.timeout_seconds,
                )
                response.raise_for_status()
                latency_ms = int((time.monotonic() - started) * 1000)
                try:
                    return response.json()
                except ValueError as exc:
                    if attempt == self.max_retries:
                        raise ProviderException(
                            f"{method} {url} parse failure: {exc}",
                            error_type="parse",
                            http_status=response.status_code,
                            latency_ms=latency_ms,
                        ) from exc
                    time.sleep(2 ** (attempt - 1))
                    continue
            except httpx.TimeoutException as exc:
                latency_ms = int((time.monotonic() - started) * 1000)
                if attempt == self.max_retries:
                    raise ProviderException(
                        f"{method} {url} timeout: {exc}",
                        error_type="timeout",
                        latency_ms=latency_ms,
                    ) from exc
                time.sleep(2 ** (attempt - 1))
            except httpx.HTTPStatusError as exc:
                status_code = exc.response.status_code if exc.response is not None else None
                latency_ms = int((time.monotonic() - started) * 1000)
                if attempt == self.max_retries:
                    raise ProviderException(
                        f"{method} {url} status {status_code}",
                        error_type=classify_http_status(status_code),
                        http_status=status_code,
                        latency_ms=latency_ms,
                    ) from exc
                time.sleep(2 ** (attempt - 1))
            except httpx.RequestError as exc:
                latency_ms = int((time.monotonic() - started) * 1000)
                if attempt == self.max_retries:
                    raise ProviderException(
                        f"{method} {url} request error: {exc}",
                        error_type="timeout",
                        latency_ms=latency_ms,
                    ) from exc
                time.sleep(2 ** (attempt - 1))

        raise ProviderException(f"{method} {url} exhausted retries.")

    def _cache_key(self, prefix: str, payload: dict[str, Any]) -> str:
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()
        return f"{prefix}:{digest}"

    def cached_query(self, prefix: str, payload: dict[str, Any], fetcher, ttl: int = 900):  # noqa: ANN001, ANN201
        cache_key = self._cache_key(prefix, payload)
        cached = cache.get(cache_key)
        if cached is not None:
            return cached
        value = fetcher()
        cache.set(cache_key, value, ttl)
        return value


class FlightProvider(ProviderMixin, ABC):
    name = "base_flight"

    @abstractmethod
    def search_flights(self, query: FlightSearchQuery) -> list[NormalizedFlightOption]:
        raise NotImplementedError

    @abstractmethod
    def get_deeplink(self, offer: dict[str, Any]) -> str:
        raise NotImplementedError


class HotelProvider(ProviderMixin, ABC):
    name = "base_hotel"

    @abstractmethod
    def search_hotels(self, query: HotelSearchQuery) -> list[NormalizedHotelOption]:
        raise NotImplementedError

    @abstractmethod
    def get_deeplink(self, hotel_offer: dict[str, Any], query: HotelSearchQuery) -> str:
        raise NotImplementedError


def parse_iso_duration_minutes(value: str | None) -> int:
    if not value:
        return 0
    try:
        duration = isodate.parse_duration(value)
        seconds = duration.total_seconds() if hasattr(duration, "total_seconds") else float(duration)
        return int(seconds // 60)
    except Exception:
        return 0


def parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        if value.endswith("Z"):
            value = value.replace("Z", "+00:00")
        return datetime.fromisoformat(value)
    except ValueError:
        return None
