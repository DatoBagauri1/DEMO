import hashlib
import os
import time
from decimal import Decimal
from typing import Any
from urllib.parse import quote_plus

from planner.services.providers.base import HotelProvider, HotelSearchQuery, NormalizedHotelOption


class ExpediaRapidHotelsProvider(HotelProvider):
    name = "expedia_rapid"

    def __init__(self) -> None:
        self.api_key = os.getenv("EXPEDIA_RAPID_KEY", "")
        self.api_secret = os.getenv("EXPEDIA_RAPID_SECRET", "")
        self.base_url = os.getenv("EXPEDIA_RAPID_BASE_URL", "https://test.ean.com").rstrip("/")
        self.point_of_sale = os.getenv("EXPEDIA_RAPID_POS", "US")

    def _auth_headers(self) -> dict[str, str]:
        timestamp = str(int(time.time()))
        signature = hashlib.sha512(f"{self.api_key}{self.api_secret}{timestamp}".encode("utf-8")).hexdigest()
        return {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": f"EAN APIKey={self.api_key},Signature={signature},timestamp={timestamp}",
            "Customer-Ip": "127.0.0.1",
            "Accept-Encoding": "gzip",
        }

    def _region_id_for_city(self, query: HotelSearchQuery) -> str | None:
        params = {
            "keyword": query.city_name,
            "language": "en-US",
            "country_code": query.country_code,
        }
        response = self._request_json(
            "GET",
            f"{self.base_url}/v3/regions",
            headers=self._auth_headers(),
            params=params,
        )
        regions = response.get("data") if isinstance(response, dict) else response
        if isinstance(regions, list) and regions:
            return str(regions[0].get("id") or regions[0].get("region_id"))
        return None

    def search_hotels(self, query: HotelSearchQuery) -> list[NormalizedHotelOption]:
        region_id = self._region_id_for_city(query)
        if not region_id:
            return []

        params: dict[str, Any] = {
            "region_id": region_id,
            "checkin": query.checkin.isoformat(),
            "checkout": query.checkout.isoformat(),
            "adults": query.adults,
            "currency": query.currency,
            "language": "en-US",
            "country_code": query.country_code,
            "supply_source": "expedia",
            "sales_channel": "website",
            "sales_environment": "hotel_only",
            "partner_point_of_sale": self.point_of_sale,
            "rate_plan_count": 1,
        }
        if query.stars_min:
            params["star_rating_min"] = query.stars_min
        if query.guest_rating_min:
            params["guest_rating_min"] = query.guest_rating_min
        if query.budget_max:
            params["price_max"] = str(query.budget_max)

        payload = query.cache_payload() | {"region_id": region_id}

        def _fetch():
            return self._request_json(
                "GET",
                f"{self.base_url}/v3/properties/availability",
                headers=self._auth_headers(),
                params=params,
            )

        response = self.cached_query(f"rapid:{self.name}", payload, _fetch, ttl=900)
        properties = response.get("data", response.get("properties", []))
        normalized = [self._normalize_property(item, query) for item in properties]
        filtered = [item for item in normalized if item.total_price > 0]
        filtered.sort(key=lambda item: item.total_price)
        return filtered[:25]

    def _normalize_property(self, prop: dict[str, Any], query: HotelSearchQuery) -> NormalizedHotelOption:
        coordinates = prop.get("location", {}).get("coordinates", {})
        lead_price = (
            prop.get("price", {})
            .get("lead", {})
            .get("amount")
            or prop.get("price", {})
            .get("totals", {})
            .get("inclusive", {})
            .get("request_currency", {})
            .get("value")
            or 0
        )
        amenities: list[str] = []
        for entry in prop.get("amenities", []):
            if isinstance(entry, dict) and entry.get("name"):
                amenities.append(entry["name"])
            elif isinstance(entry, str):
                amenities.append(entry)

        neighborhood = (
            prop.get("location", {})
            .get("address", {})
            .get("city", "")
            or prop.get("location", {}).get("neighborhood", "")
        )
        return NormalizedHotelOption(
            provider=self.name,
            external_offer_id=str(prop.get("property_id") or prop.get("id") or ""),
            name=prop.get("name", "Unknown hotel"),
            star_rating=float(prop.get("ratings", {}).get("property", prop.get("star_rating", 0)) or 0),
            guest_rating=float(prop.get("ratings", {}).get("guest", prop.get("guest_rating", 0)) or 0),
            neighborhood=neighborhood,
            latitude=coordinates.get("latitude"),
            longitude=coordinates.get("longitude"),
            amenities=amenities[:8],
            currency=prop.get("price", {}).get("currency", query.currency),
            total_price=Decimal(str(lead_price)),
            deeplink_url=self.get_deeplink(prop, query),
            raw_payload=prop,
        )

    def get_deeplink(self, hotel_offer: dict[str, Any], query: HotelSearchQuery) -> str:
        links = hotel_offer.get("links", {})
        if isinstance(links, dict):
            web_link = links.get("web") or links.get("booking") or links.get("self")
            if web_link:
                return web_link
        destination = quote_plus(f"{query.city_name}, {query.country_code}")
        return (
            "https://www.expedia.com/Hotel-Search"
            f"?destination={destination}&startDate={query.checkin.isoformat()}"
            f"&endDate={query.checkout.isoformat()}&adults={query.adults}"
        )

