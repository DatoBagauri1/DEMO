from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from rest_framework import serializers

from planner.models import FlightOption, HotelOption, PackageOption, PlanRequest, SavedPackage, SavedPlace, TourOption
from planner.services.airports import airport_exists, normalize_iata, resolve_origin_code
from planner.services.security import is_allowed_outbound_url

PREFERENCE_KEYS = ("beach", "nature", "culture", "nightlife", "food", "quiet", "family", "luxury", "adventure")
REMOVED_FLIGHT_FILTER_KEYS = {"cabin", "cabin_class", "departure_type", "travel_class"}


def _sanitize_flight_filters_payload(value) -> dict:  # noqa: ANN201
    if not isinstance(value, dict):
        return {}
    cleaned = dict(value)
    for key in REMOVED_FLIGHT_FILTER_KEYS:
        cleaned.pop(key, None)
    return cleaned


class PlanStartSerializer(serializers.Serializer):
    origin_iata = serializers.CharField(max_length=3, required=False, allow_blank=True)
    origin_input = serializers.CharField(max_length=64, required=False, allow_blank=True)
    search_mode = serializers.ChoiceField(choices=PlanRequest.SearchMode.values, required=False, default=PlanRequest.SearchMode.DIRECT)
    destination_iata = serializers.CharField(max_length=3, required=False, allow_blank=True)
    destination_iatas = serializers.ListField(child=serializers.CharField(max_length=3), required=False, allow_empty=True)
    destination_input = serializers.CharField(max_length=64, required=False, allow_blank=True)
    destination_country = serializers.CharField(max_length=2, required=False, allow_blank=True)

    date_mode = serializers.ChoiceField(choices=PlanRequest.DateMode.values, required=False, default=PlanRequest.DateMode.EXACT)
    depart_date = serializers.DateField(required=False, allow_null=True)
    return_date = serializers.DateField(required=False, allow_null=True)
    departure_date_from = serializers.DateField(required=False, allow_null=True)
    departure_date_to = serializers.DateField(required=False, allow_null=True)
    travel_month = serializers.DateField(required=False, allow_null=True)

    trip_length_min = serializers.IntegerField(min_value=1, max_value=30, required=False, default=3)
    trip_length_max = serializers.IntegerField(min_value=1, max_value=45, required=False, default=7)
    nights_min = serializers.IntegerField(min_value=1, max_value=30, required=False)
    nights_max = serializers.IntegerField(min_value=1, max_value=45, required=False)

    adults = serializers.IntegerField(min_value=1, max_value=9, required=False, default=1)
    children = serializers.IntegerField(min_value=0, max_value=9, required=False, default=0)
    travelers = serializers.IntegerField(min_value=1, max_value=12, required=False)

    search_currency = serializers.CharField(max_length=3, required=False, default="USD")

    flexibility_days = serializers.IntegerField(min_value=0, max_value=30, required=False, default=0)
    hotel_filters = serializers.JSONField(required=False, default=dict)
    flight_filters = serializers.JSONField(required=False, default=dict)
    explore_constraints = serializers.JSONField(required=False, default=dict)
    preferences = serializers.JSONField(required=False, default=dict)

    def validate_origin_iata(self, value: str) -> str:
        probe = normalize_iata(value)
        if not probe:
            return ""
        if len(probe) != 3 or not airport_exists(probe):
            raise serializers.ValidationError("Unknown origin airport code.")
        return probe

    def validate_destination_iata(self, value: str) -> str:
        probe = normalize_iata(value)
        if not probe:
            return ""
        if len(probe) != 3 or not airport_exists(probe):
            raise serializers.ValidationError("Unknown destination airport code.")
        return probe

    def validate_destination_country(self, value: str) -> str:
        return (value or "").strip().upper()[:2]

    def validate_preferences(self, value):  # noqa: ANN201
        if value in (None, ""):
            return {}
        if isinstance(value, list):
            normalized = {}
            for item in value:
                key = str(item).strip().lower()
                if key in PREFERENCE_KEYS:
                    normalized[key] = 1.0
            return normalized
        if isinstance(value, dict):
            normalized = {}
            for key, raw in value.items():
                label = str(key).strip().lower()
                if label not in PREFERENCE_KEYS:
                    continue
                try:
                    score = float(raw)
                except (TypeError, ValueError):
                    score = 1.0 if bool(raw) else 0.0
                normalized[label] = max(0.0, min(1.0, score))
            return normalized
        raise serializers.ValidationError("preferences must be a JSON object or array.")

    def validate_flight_filters(self, value):  # noqa: ANN201
        return _sanitize_flight_filters_payload(value)

    def validate_destination_iatas(self, value):  # noqa: ANN201
        cleaned = []
        seen = set()
        for raw in value or []:
            code = normalize_iata(raw)
            if not code or code in seen:
                continue
            if not airport_exists(code):
                raise serializers.ValidationError(f"Unknown destination airport code: {code}")
            seen.add(code)
            cleaned.append(code)
        return cleaned

    def validate(self, attrs):  # noqa: ANN201
        raw_input = getattr(self, "initial_data", {}) or {}
        if "total_budget" in raw_input:
            raise serializers.ValidationError({"total_budget": "Budget is no longer accepted. Totals are computed from concrete offers."})

        origin_probe = normalize_iata(attrs.get("origin_iata") or attrs.get("origin_input") or "")
        if not origin_probe:
            raise serializers.ValidationError({"origin_iata": "Origin airport is required."})

        if not airport_exists(origin_probe):
            resolved = resolve_origin_code(origin_probe)
            if not resolved or not airport_exists(resolved):
                raise serializers.ValidationError({"origin_iata": "Unknown origin airport code."})
            origin_probe = resolved
        attrs["origin_iata"] = origin_probe
        attrs["origin_input"] = attrs.get("origin_input") or origin_probe

        search_mode = attrs.get("search_mode") or PlanRequest.SearchMode.DIRECT
        destination_iata = normalize_iata(attrs.get("destination_iata") or "")
        destination_iatas = list(attrs.get("destination_iatas") or [])
        if destination_iata and destination_iata not in destination_iatas:
            destination_iatas.insert(0, destination_iata)
        if not destination_iata and destination_iatas:
            destination_iata = destination_iatas[0]

        if search_mode == PlanRequest.SearchMode.DIRECT and not destination_iatas:
            if attrs.get("destination_country"):
                search_mode = PlanRequest.SearchMode.EXPLORE
                attrs["search_mode"] = search_mode
            else:
                raise serializers.ValidationError({"destination_iata": "Destination airport is required in direct mode."})

        for code in destination_iatas:
            if code == origin_probe:
                raise serializers.ValidationError({"destination_iata": "Destination must be different from origin."})

        attrs["destination_iata"] = destination_iata
        attrs["destination_iatas"] = destination_iatas

        # Date validation and compatibility with legacy exact mode.
        depart_date = attrs.get("depart_date")
        return_date = attrs.get("return_date")
        date_from = attrs.get("departure_date_from")
        date_to = attrs.get("departure_date_to")

        if depart_date and return_date:
            if return_date <= depart_date:
                raise serializers.ValidationError({"return_date": "Return date must be after departure date."})
            attrs["departure_date_from"] = depart_date
            attrs["departure_date_to"] = depart_date
            attrs["trip_length_min"] = (return_date - depart_date).days
            attrs["trip_length_max"] = (return_date - depart_date).days
        elif date_from or date_to:
            if not date_from or not date_to:
                raise serializers.ValidationError({"departure_date_from": "Both departure_date_from and departure_date_to are required."})
            if date_to < date_from:
                raise serializers.ValidationError({"departure_date_to": "departure_date_to must be on/after departure_date_from."})
        elif attrs.get("travel_month"):
            month = attrs["travel_month"]
            attrs["departure_date_from"] = month.replace(day=1)
            attrs["departure_date_to"] = month.replace(day=1) + timedelta(days=27)
        else:
            raise serializers.ValidationError({"departure_date_from": "Provide exact dates, departure range, or travel_month."})

        if attrs.get("nights_min") is not None:
            attrs["trip_length_min"] = int(attrs["nights_min"])
        if attrs.get("nights_max") is not None:
            attrs["trip_length_max"] = int(attrs["nights_max"])

        if attrs["trip_length_max"] < attrs["trip_length_min"]:
            raise serializers.ValidationError({"trip_length_max": "trip_length_max must be >= trip_length_min."})

        travelers = attrs.get("travelers")
        if travelers and not attrs.get("adults"):
            attrs["adults"] = int(travelers)
        if attrs.get("adults", 1) + attrs.get("children", 0) <= 0:
            raise serializers.ValidationError({"adults": "At least one traveler is required."})

        attrs["search_currency"] = attrs.get("search_currency", "USD").upper().strip()
        attrs["total_budget"] = Decimal("0.00")
        attrs["preferences"] = self.validate_preferences(attrs.get("preferences", {}))
        return attrs


class PlanStatusSerializer(serializers.ModelSerializer):
    class Meta:
        model = PlanRequest
        fields = (
            "id",
            "status",
            "progress_message",
            "progress_percent",
            "error_message",
            "created_at",
            "completed_at",
            "public_token",
        )


class FlightOptionSerializer(serializers.ModelSerializer):
    outbound_url = serializers.CharField(source="deeplink_url", read_only=True)
    fallback_search = serializers.SerializerMethodField()

    def get_fallback_search(self, obj: FlightOption) -> bool:
        raw = obj.raw_payload or {}
        if "fallback_search" in raw:
            return bool(raw.get("fallback_search"))
        return str(obj.link_type or "").lower() != "item"

    class Meta:
        model = FlightOption
        fields = (
            "id",
            "provider",
            "external_offer_id",
            "origin_airport",
            "destination_airport",
            "departure_at",
            "return_at",
            "airline_codes",
            "stops",
            "duration_minutes",
            "currency",
            "total_price",
            "amount_minor",
            "last_checked_at",
            "deeplink_url",
            "outbound_url",
            "link_type",
            "fallback_search",
            "link_confidence",
            "link_rationale",
        )


class HotelOptionSerializer(serializers.ModelSerializer):
    outbound_url = serializers.CharField(source="deeplink_url", read_only=True)
    fallback_search = serializers.SerializerMethodField()

    def get_fallback_search(self, obj: HotelOption) -> bool:
        raw = obj.raw_payload or {}
        if "fallback_search" in raw:
            return bool(raw.get("fallback_search"))
        return str(obj.link_type or "").lower() != "item"

    class Meta:
        model = HotelOption
        fields = (
            "id",
            "provider",
            "external_offer_id",
            "provider_property_id",
            "name",
            "star_rating",
            "guest_rating",
            "neighborhood",
            "latitude",
            "longitude",
            "amenities",
            "currency",
            "total_price",
            "amount_minor",
            "distance_km",
            "last_checked_at",
            "deeplink_url",
            "outbound_url",
            "link_type",
            "fallback_search",
            "link_confidence",
            "link_rationale",
        )


class TourOptionSerializer(serializers.ModelSerializer):
    outbound_url = serializers.CharField(source="deeplink_url", read_only=True)
    fallback_search = serializers.SerializerMethodField()

    def get_fallback_search(self, obj: TourOption) -> bool:
        raw = obj.raw_payload or {}
        if "fallback_search" in raw:
            return bool(raw.get("fallback_search"))
        return str(obj.link_type or "").lower() != "item"

    class Meta:
        model = TourOption
        fields = (
            "id",
            "provider",
            "external_product_id",
            "name",
            "currency",
            "total_price",
            "amount_minor",
            "deeplink_url",
            "outbound_url",
            "link_type",
            "fallback_search",
            "link_confidence",
            "link_rationale",
            "last_checked_at",
        )


class PackageOptionSerializer(serializers.ModelSerializer):
    flight = FlightOptionSerializer(source="flight_option")
    hotel = HotelOptionSerializer(source="hotel_option")
    tour_options = TourOptionSerializer(many=True, read_only=True)
    destination = serializers.SerializerMethodField()
    deeplinks = serializers.SerializerMethodField()
    outbound_links = serializers.SerializerMethodField()
    components = serializers.SerializerMethodField()
    freshness_timestamp = serializers.DateTimeField(source="freshness_at", read_only=True)
    estimated_hotel_min = serializers.DecimalField(max_digits=10, decimal_places=2, source="estimated_hotel_nightly_min", read_only=True)
    estimated_hotel_max = serializers.DecimalField(max_digits=10, decimal_places=2, source="estimated_hotel_nightly_max", read_only=True)
    package_total = serializers.DecimalField(max_digits=10, decimal_places=2, source="total_price", read_only=True)
    saved = serializers.SerializerMethodField()
    price_age_seconds = serializers.IntegerField(read_only=True)
    flights = serializers.SerializerMethodField()
    hotels = serializers.SerializerMethodField()
    tours = serializers.SerializerMethodField()
    places = serializers.SerializerMethodField()

    class Meta:
        model = PackageOption
        fields = (
            "id",
            "rank",
            "destination",
            "currency",
            "package_total",
            "price_breakdown",
            "estimated_total_min",
            "estimated_total_max",
            "estimated_flight_min",
            "estimated_flight_max",
            "estimated_hotel_nightly_min",
            "estimated_hotel_nightly_max",
            "estimated_hotel_min",
            "estimated_hotel_max",
            "freshness_timestamp",
            "deeplinks",
            "outbound_links",
            "component_links",
            "component_summary",
            "components",
            "score",
            "score_breakdown",
            "total_price",
            "amount_minor",
            "price_score",
            "convenience_score",
            "quality_score",
            "location_score",
            "explanations",
            "last_scored_at",
            "price_age_seconds",
            "flight",
            "hotel",
            "tour_options",
            "flights",
            "hotels",
            "tours",
            "places",
            "saved",
        )

    def get_destination(self, obj: PackageOption) -> dict[str, str]:
        metadata = obj.candidate.metadata or {}
        return {
            "country": obj.candidate.country_code,
            "city": obj.candidate.city_name,
            "airport": obj.candidate.airport_code,
            "airport_name": str(metadata.get("airport_name") or ""),
        }

    def get_deeplinks(self, obj: PackageOption) -> dict[str, str | None]:
        component_links = obj.component_links or {}
        flight_url = str((component_links.get("flight") or {}).get("outbound_url") or obj.flight_url or obj.flight_option.deeplink_url or "")
        hotel_url = str((component_links.get("hotel") or {}).get("outbound_url") or obj.hotel_url or obj.hotel_option.deeplink_url or "")
        tour_urls = [
            str(item.get("outbound_url") or "")
            for item in (component_links.get("tours") or [])
            if isinstance(item, dict) and str(item.get("outbound_url") or "")
        ]
        return {
            "flight_url": flight_url or None,
            "hotel_url": hotel_url or None,
            "tours_url": (obj.tours_url or (tour_urls[0] if tour_urls else None)),
            "flight_outbound_url": flight_url or None,
            "hotel_outbound_url": hotel_url or None,
            "tour_outbound_urls": tour_urls,
        }

    def get_outbound_links(self, obj: PackageOption) -> dict:
        deeplinks = self.get_deeplinks(obj)
        return {
            "flight": deeplinks.get("flight_outbound_url"),
            "hotel": deeplinks.get("hotel_outbound_url"),
            "tours": deeplinks.get("tour_outbound_urls") or ([deeplinks.get("tours_url")] if deeplinks.get("tours_url") else []),
        }

    def get_components(self, obj: PackageOption) -> dict:
        summary = obj.component_summary or {}
        if summary:
            return summary
        tours = self.get_tours(obj)[:3]
        return {
            "flight": self.get_flights(obj)[0] if self.get_flights(obj) else None,
            "hotel": self.get_hotels(obj)[0] if self.get_hotels(obj) else None,
            "tours": tours,
        }

    def get_saved(self, obj: PackageOption) -> bool:
        request = self.context.get("request")
        user = getattr(request, "user", None) if request else None
        if not user or not user.is_authenticated:
            return False
        return SavedPackage.objects.filter(user=user, package=obj).exists()

    def _normalize_entities(self, items: list[dict], fallback_kind: str) -> list[dict]:
        normalized = []
        for raw in items or []:
            if not isinstance(raw, dict):
                continue
            title = str(raw.get("title") or raw.get("name") or "").strip()
            link = str(raw.get("outbound_url") or raw.get("link") or raw.get("url") or "").strip()
            if not title or not link:
                continue
            confidence = raw.get("confidence")
            try:
                confidence_value = float(confidence) if confidence is not None else None
            except (TypeError, ValueError):
                confidence_value = None
            link_type = str(raw.get("link_type") or ("item" if fallback_kind == "place" else "search")).strip().lower()
            fallback_search = raw.get("fallback_search")
            if fallback_search is None:
                fallback_search = link_type != "item"
            normalized.append(
                {
                    "title": title,
                    "name": title,
                    "price": raw.get("price"),
                    "currency": raw.get("currency"),
                    "link": link,
                    "outbound_url": link,
                    "deeplink_url": link,
                    "image_url": str(raw.get("image_url") or raw.get("image") or "").strip(),
                    "provider": str(raw.get("provider") or "travelpayouts"),
                    "kind": str(raw.get("kind") or fallback_kind),
                    "description": str(raw.get("description") or "").strip(),
                    "link_type": link_type,
                    "fallback_search": bool(fallback_search),
                    "rationale": str(raw.get("rationale") or ""),
                    "confidence": confidence_value,
                    "stable_id": str(raw.get("stable_id") or raw.get("provider_property_id") or raw.get("id") or ""),
                    "provider_property_id": str(raw.get("provider_property_id") or ""),
                },
            )
        return normalized

    def get_flights(self, obj: PackageOption) -> list[dict]:
        return self._normalize_entities(obj.flight_entities, "flight")

    def get_hotels(self, obj: PackageOption) -> list[dict]:
        return self._normalize_entities(obj.hotel_entities, "hotel")

    def get_tours(self, obj: PackageOption) -> list[dict]:
        return self._normalize_entities(obj.tour_entities, "tour")

    def get_places(self, obj: PackageOption) -> list[dict]:
        return self._normalize_entities(obj.place_entities, "place")


class SavedPlaceSerializer(serializers.ModelSerializer):
    class Meta:
        model = SavedPlace
        fields = (
            "id",
            "name",
            "city",
            "country",
            "lat",
            "lon",
            "source",
            "external_id",
            "image_url",
            "outbound_url",
            "notes",
            "created_at",
            "updated_at",
        )


class SavedPlaceToggleSerializer(serializers.Serializer):
    saved_place_id = serializers.IntegerField(required=False)
    name = serializers.CharField(max_length=255, required=False, allow_blank=True)
    city = serializers.CharField(max_length=128, required=False, allow_blank=True, allow_null=True)
    country = serializers.CharField(max_length=128, required=False, allow_blank=True, allow_null=True)
    lat = serializers.FloatField(required=False, allow_null=True)
    lon = serializers.FloatField(required=False, allow_null=True)
    source = serializers.CharField(max_length=32, required=False, allow_blank=True, default="manual")
    external_id = serializers.CharField(max_length=255, required=False, allow_blank=True, allow_null=True)
    image_url = serializers.URLField(max_length=1500, required=False, allow_blank=True, allow_null=True)
    outbound_url = serializers.URLField(max_length=1500, required=False, allow_blank=True, allow_null=True)
    notes = serializers.CharField(required=False, allow_blank=True, allow_null=True, max_length=5000)

    def validate_outbound_url(self, value: str | None) -> str | None:
        if value in (None, ""):
            return None
        if not is_allowed_outbound_url(value):
            raise serializers.ValidationError("Outbound URL is not allowed.")
        return value

    def validate(self, attrs):  # noqa: ANN201
        saved_place_id = attrs.get("saved_place_id")
        if saved_place_id:
            return attrs

        name = str(attrs.get("name") or "").strip()
        if not name:
            raise serializers.ValidationError({"name": "name is required when saved_place_id is not provided."})
        attrs["name"] = name

        for field in ("city", "country", "external_id", "image_url", "outbound_url", "notes"):
            value = attrs.get(field)
            if isinstance(value, str):
                value = value.strip()
                attrs[field] = value or None

        attrs["source"] = str(attrs.get("source") or "manual").strip().lower()[:32] or "manual"
        return attrs
