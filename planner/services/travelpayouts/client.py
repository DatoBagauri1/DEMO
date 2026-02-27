from __future__ import annotations

from datetime import date
from time import monotonic
from typing import Any

from planner.services.config import travelpayouts_api_token
from planner.services.providers.base import ProviderException, ProviderMixin


class TravelpayoutsClient(ProviderMixin):
    name = "travelpayouts"
    timeout_seconds = 8
    max_retries = 2

    def __init__(self, *, token: str | None = None, base_url: str = "https://api.travelpayouts.com") -> None:
        self.base_url = base_url.rstrip("/")
        self.token = (token or travelpayouts_api_token()).strip()

    @property
    def enabled(self) -> bool:
        return bool(self.token)

    @property
    def headers(self) -> dict[str, str]:
        return {
            "Accept": "application/json",
            "X-Access-Token": self.token,
        }

    def _get_json(self, path: str, params: dict[str, Any], cache_key: str) -> tuple[dict[str, Any], int]:
        if not self.enabled:
            raise ProviderException(
                "Travelpayouts token is not configured.",
                error_type="auth",
                http_status=401,
            )

        def _fetch() -> dict[str, Any]:
            started = monotonic()
            payload = self._request_json(
                "GET",
                f"{self.base_url}{path}",
                headers=self.headers,
                params=params,
            )
            latency = int((monotonic() - started) * 1000)
            payload["_latency_ms"] = latency
            return payload

        cache_payload = {"path": path, "params": params}
        payload = self.cached_query(cache_key, cache_payload, _fetch, ttl=900)
        latency_ms = int(payload.get("_latency_ms") or 0)
        if "_latency_ms" in payload:
            payload = {k: v for k, v in payload.items() if k != "_latency_ms"}
        return payload, latency_ms

    def get_cheap_prices(
        self,
        *,
        origin: str,
        destination: str,
        depart_date: date,
        return_date: date | None,
        currency: str,
    ) -> tuple[dict[str, Any], int]:
        params: dict[str, Any] = {
            "origin": origin,
            "destination": destination,
            "depart_date": depart_date.isoformat(),
            "currency": currency,
        }
        if return_date:
            params["return_date"] = return_date.isoformat()
        return self._get_json("/v1/prices/cheap", params, "travelpayouts:cheap")

    def get_calendar_prices(
        self,
        *,
        origin: str,
        destination: str,
        depart_date: date,
        return_date: date | None,
        currency: str,
    ) -> tuple[dict[str, Any], int]:
        params: dict[str, Any] = {
            "origin": origin,
            "destination": destination,
            "depart_date": depart_date.isoformat(),
            "currency": currency,
        }
        if return_date:
            params["return_date"] = return_date.isoformat()
        return self._get_json("/v1/prices/calendar", params, "travelpayouts:calendar")

    def get_city_directions(
        self,
        *,
        origin: str,
        currency: str,
    ) -> tuple[dict[str, Any], int]:
        params = {
            "origin": origin,
            "currency": currency,
        }
        return self._get_json("/v1/city-directions", params, "travelpayouts:directions")
