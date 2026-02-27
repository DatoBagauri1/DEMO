import os
from decimal import Decimal
from typing import Any

from planner.services.providers.base import (
    FlightProvider,
    FlightSearchQuery,
    NormalizedFlightOption,
    parse_datetime,
    parse_iso_duration_minutes,
)


class DuffelFlightsProvider(FlightProvider):
    name = "duffel"

    def __init__(self) -> None:
        self.token = os.getenv("DUFFEL_ACCESS_TOKEN", "")
        self.base_url = os.getenv("DUFFEL_BASE_URL", "https://api.duffel.com").rstrip("/")

    @property
    def headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token}",
            "Duffel-Version": "v2",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def search_flights(self, query: FlightSearchQuery) -> list[NormalizedFlightOption]:
        slices = [
            {
                "origin": query.origin,
                "destination": query.destination,
                "departure_date": query.depart_date.isoformat(),
            },
        ]
        if query.return_date:
            slices.append(
                {
                    "origin": query.destination,
                    "destination": query.origin,
                    "departure_date": query.return_date.isoformat(),
                },
            )

        payload = {
            "data": {
                "slices": slices,
                "passengers": [{"type": "adult"} for _ in range(query.travelers)],
                "cabin_class": query.cabin.lower(),
                "return_offers": True,
            },
        }

        def _fetch():
            return self._request_json(
                "POST",
                f"{self.base_url}/air/offer_requests",
                headers=self.headers,
                json_body=payload,
            )

        response = self.cached_query(f"duffel:{self.name}", payload, _fetch, ttl=900)
        offers = self._extract_offers(response)
        normalized = [self._normalize_offer(offer, query.currency) for offer in offers]
        filtered = [item for item in normalized if item.total_price > 0]
        filtered.sort(key=lambda item: item.total_price)
        return filtered[:25]

    def _extract_offers(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        offers: list[dict[str, Any]] = []
        included = payload.get("included", [])
        for item in included:
            if item.get("type") == "offer":
                offers.append(item)
        if offers:
            return offers

        data = payload.get("data")
        if isinstance(data, dict):
            possible = data.get("offers", [])
            if isinstance(possible, list):
                return possible
        return []

    def _normalize_offer(self, offer: dict[str, Any], fallback_currency: str) -> NormalizedFlightOption:
        slices = offer.get("slices", [])
        first_segments = slices[0].get("segments", []) if slices else []
        outbound_first = first_segments[0] if first_segments else {}
        outbound_last = first_segments[-1] if first_segments else {}
        inbound_segments = slices[1].get("segments", []) if len(slices) > 1 else []
        inbound_last = inbound_segments[-1] if inbound_segments else {}

        duration_minutes = sum(parse_iso_duration_minutes(slice_item.get("duration")) for slice_item in slices)
        stops = sum(max(0, len(slice_item.get("segments", [])) - 1) for slice_item in slices)

        airlines: list[str] = []
        for slice_item in slices:
            for segment in slice_item.get("segments", []):
                code = (
                    segment.get("operating_carrier", {}).get("iata_code")
                    or segment.get("marketing_carrier", {}).get("iata_code")
                    or segment.get("marketing_carrier_iata_code")
                )
                if code and code not in airlines:
                    airlines.append(code)

        return NormalizedFlightOption(
            provider=self.name,
            external_offer_id=str(offer.get("id", "")),
            origin_airport=outbound_first.get("origin", {}).get("iata_code", ""),
            destination_airport=outbound_last.get("destination", {}).get("iata_code", ""),
            departure_at=parse_datetime(outbound_first.get("departing_at")),
            return_at=parse_datetime(inbound_last.get("arriving_at")),
            airline_codes=airlines,
            stops=stops,
            duration_minutes=duration_minutes,
            cabin_class=offer.get("cabin_class", "economy"),
            currency=offer.get("total_currency", fallback_currency),
            total_price=Decimal(str(offer.get("total_amount", "0"))),
            deeplink_url=self.get_deeplink(offer),
            raw_payload=offer,
        )

    def get_deeplink(self, offer: dict[str, Any]) -> str:
        return (
            offer.get("booking_url")
            or offer.get("deeplink")
            or offer.get("links", {}).get("self")
            or f"{self.base_url}/air/offers/{offer.get('id', '')}"
        )

