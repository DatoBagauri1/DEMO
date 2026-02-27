import os
from datetime import timedelta
from decimal import Decimal

from django.core.cache import cache

from planner.services.providers.base import (
    FlightProvider,
    FlightSearchQuery,
    NormalizedFlightOption,
    parse_datetime,
    parse_iso_duration_minutes,
)


class AmadeusFlightsProvider(FlightProvider):
    name = "amadeus"

    def __init__(self) -> None:
        self.client_id = os.getenv("AMADEUS_CLIENT_ID", "")
        self.client_secret = os.getenv("AMADEUS_CLIENT_SECRET", "")
        self.base_url = os.getenv("AMADEUS_BASE_URL", "https://test.api.amadeus.com").rstrip("/")

    def _token_cache_key(self) -> str:
        return f"amadeus:token:{self.client_id}"

    def _get_access_token(self) -> str:
        cache_key = self._token_cache_key()
        token = cache.get(cache_key)
        if token:
            return token

        payload = {
            "grant_type": "client_credentials",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
        }
        data = self._request_json(
            "POST",
            f"{self.base_url}/v1/security/oauth2/token",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data=payload,
        )
        token = data["access_token"]
        expires_in = int(data.get("expires_in", 1200))
        cache.set(cache_key, token, timeout=max(60, expires_in - 60))
        return token

    def search_flights(self, query: FlightSearchQuery) -> list[NormalizedFlightOption]:
        token = self._get_access_token()
        params = {
            "originLocationCode": query.origin,
            "destinationLocationCode": query.destination,
            "departureDate": query.depart_date.isoformat(),
            "adults": query.travelers,
            "currencyCode": query.currency,
            "max": 30,
            "travelClass": query.cabin.upper(),
        }
        if query.return_date:
            params["returnDate"] = query.return_date.isoformat()
        if query.max_stops == 0:
            params["nonStop"] = "true"

        def _fetch():
            return self._request_json(
                "GET",
                f"{self.base_url}/v2/shopping/flight-offers",
                headers={"Authorization": f"Bearer {token}"},
                params=params,
            )

        response = self.cached_query(f"amadeus:{self.name}", params, _fetch, ttl=900)
        offers = response.get("data", [])

        normalized = [self._normalize_offer(item, query.currency) for item in offers]
        filtered = [item for item in normalized if item.total_price > 0]
        filtered.sort(key=lambda item: item.total_price)
        return filtered[:25]

    def _normalize_offer(self, offer: dict, fallback_currency: str) -> NormalizedFlightOption:
        itineraries = offer.get("itineraries", [])
        outbound = itineraries[0] if itineraries else {}
        inbound = itineraries[1] if len(itineraries) > 1 else {}
        outbound_segments = outbound.get("segments", [])
        inbound_segments = inbound.get("segments", [])
        first = outbound_segments[0] if outbound_segments else {}
        last = outbound_segments[-1] if outbound_segments else {}

        duration_minutes = parse_iso_duration_minutes(outbound.get("duration"))
        if inbound:
            duration_minutes += parse_iso_duration_minutes(inbound.get("duration"))
        if not duration_minutes and first.get("departure", {}).get("at") and last.get("arrival", {}).get("at"):
            depart = parse_datetime(first["departure"]["at"])
            arrive = parse_datetime(last["arrival"]["at"])
            if depart and arrive:
                duration_minutes = int((arrive - depart) / timedelta(minutes=1))

        stops = max(0, len(outbound_segments) - 1) + max(0, len(inbound_segments) - 1)
        airlines = offer.get("validatingAirlineCodes", [])

        return NormalizedFlightOption(
            provider=self.name,
            external_offer_id=str(offer.get("id", "")),
            origin_airport=first.get("departure", {}).get("iataCode", ""),
            destination_airport=last.get("arrival", {}).get("iataCode", ""),
            departure_at=parse_datetime(first.get("departure", {}).get("at")),
            return_at=parse_datetime(inbound_segments[-1].get("arrival", {}).get("at")) if inbound_segments else None,
            airline_codes=airlines,
            stops=stops,
            duration_minutes=max(0, duration_minutes),
            cabin_class=offer.get("travelerPricings", [{}])[0].get("fareOption", "economy").lower(),
            currency=offer.get("price", {}).get("currency", fallback_currency),
            total_price=Decimal(str(offer.get("price", {}).get("grandTotal", "0"))),
            deeplink_url=self.get_deeplink(offer),
            raw_payload=offer,
        )

    def get_deeplink(self, offer: dict) -> str:
        links = offer.get("links", {})
        return links.get("flightOffersPricing") or links.get("self") or f"{self.base_url}/v2/shopping/flight-offers/{offer.get('id', '')}"

