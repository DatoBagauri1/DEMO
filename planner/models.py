import uuid
from datetime import date, timedelta
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils import timezone

from planner.services.security import is_allowed_outbound_url


def token_hex() -> str:
    return uuid.uuid4().hex


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class MoneyDisplayMixin(models.Model):
    amount_minor = models.BigIntegerField(default=0)
    currency = models.CharField(max_length=3)

    class Meta:
        abstract = True

    @property
    def amount_major(self) -> Decimal:
        return Decimal(self.amount_minor) / Decimal("100")

    @property
    def amount_display(self) -> str:
        return f"{self.currency} {self.amount_major:,.2f}"


class Profile(TimeStampedModel):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="profile")
    default_origin = models.CharField(max_length=16, blank=True)
    preferred_currency = models.CharField(max_length=3, default="USD")
    default_budget_min = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("800.00"))
    default_budget_max = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("3500.00"))
    travel_preferences = models.JSONField(default=dict, blank=True)

    def __str__(self) -> str:
        return f"Profile<{self.user}>"


class Airport(TimeStampedModel):
    iata = models.CharField(max_length=3, unique=True, db_index=True)
    name = models.CharField(max_length=255)
    city = models.CharField(max_length=128, db_index=True)
    country = models.CharField(max_length=128, db_index=True)
    country_code = models.CharField(max_length=2, blank=True, db_index=True)
    latitude = models.FloatField(null=True, blank=True)
    longitude = models.FloatField(null=True, blank=True)
    timezone = models.CharField(max_length=64, blank=True)
    search_blob = models.CharField(max_length=512, blank=True, db_index=True)

    class Meta:
        ordering = ["iata"]
        indexes = [
            models.Index(fields=["iata", "city"]),
            models.Index(fields=["city", "country"]),
            models.Index(fields=["name"]),
        ]

    def __str__(self) -> str:
        return f"{self.iata} - {self.city}"

    @property
    def display_name(self) -> str:
        return f"{self.iata} - {self.name} ({self.city}, {self.country})"


class PlanRequest(TimeStampedModel):
    class DateMode(models.TextChoices):
        EXACT = "exact", "Exact dates"
        FLEXIBLE = "flexible", "Month and flexibility"

    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        VALIDATING = "validating", "Validating"
        EXPANDING_DESTINATIONS = "expanding_destinations", "Expanding destinations"
        FETCHING_FLIGHT_SIGNALS = "fetching_flight_signals", "Fetching flight signals"
        FETCHING_HOTEL_SIGNALS = "fetching_hotel_signals", "Fetching hotel signals"
        FETCHING_TOURS = "fetching_tours", "Fetching tours"
        FETCHING_PLACES = "fetching_places", "Fetching places"
        SCORING = "scoring", "Scoring"
        # Legacy status values retained for backward compatibility.
        SEARCHING_FLIGHTS = "searching_flights", "Searching flights"
        SEARCHING_HOTELS = "searching_hotels", "Searching hotels"
        BUILDING_PACKAGES = "building_packages", "Building packages"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"

    class SearchMode(models.TextChoices):
        DIRECT = "direct", "Direct destination"
        EXPLORE = "explore", "Explore destinations"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="plan_requests")
    origin_input = models.CharField(max_length=64)
    origin_code = models.CharField(max_length=8)
    origin_iata = models.CharField(max_length=3, blank=True, db_index=True)
    search_mode = models.CharField(max_length=16, choices=SearchMode.choices, default=SearchMode.DIRECT, db_index=True)
    destination_input = models.CharField(max_length=64, blank=True)
    destination_iata = models.CharField(max_length=3, blank=True, db_index=True)
    destination_iatas = models.JSONField(default=list, blank=True)
    destination_country = models.CharField(max_length=2, db_index=True)
    date_mode = models.CharField(max_length=16, choices=DateMode.choices, default=DateMode.EXACT)
    depart_date = models.DateField(null=True, blank=True)
    return_date = models.DateField(null=True, blank=True)
    travel_month = models.DateField(null=True, blank=True)
    departure_date_from = models.DateField(null=True, blank=True)
    departure_date_to = models.DateField(null=True, blank=True)
    flexibility_days = models.PositiveSmallIntegerField(default=0)
    trip_length_min = models.PositiveSmallIntegerField(default=3)
    trip_length_max = models.PositiveSmallIntegerField(default=7)
    nights_min = models.PositiveSmallIntegerField(default=3)
    nights_max = models.PositiveSmallIntegerField(default=7)
    total_budget = models.DecimalField(max_digits=10, decimal_places=2)
    travelers = models.PositiveSmallIntegerField(default=1)
    adults = models.PositiveSmallIntegerField(default=1)
    children = models.PositiveSmallIntegerField(default=0)
    search_currency = models.CharField(max_length=3, default="USD")
    hotel_filters = models.JSONField(default=dict, blank=True)
    flight_filters = models.JSONField(default=dict, blank=True)
    preference_weights = models.JSONField(default=dict, blank=True)
    explore_constraints = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=32, choices=Status.choices, default=Status.QUEUED, db_index=True)
    progress_message = models.CharField(max_length=255, blank=True)
    progress_percent = models.PositiveSmallIntegerField(default=0)
    error_message = models.TextField(blank=True)
    public_token = models.CharField(max_length=32, unique=True, default=token_hex, db_index=True)
    idempotency_key = models.CharField(max_length=64, blank=True, null=True, db_index=True)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "idempotency_key"],
                name="planner_planrequest_user_idempotency_key_unique",
            ),
        ]

    def __str__(self) -> str:
        return f"PlanRequest<{self.id}>"

    @property
    def total_travelers(self) -> int:
        if self.adults or self.children:
            return max(1, int(self.adults or 0) + int(self.children or 0))
        return max(1, int(self.travelers or 1))

    def resolve_dates(self) -> tuple[date, date]:
        if self.date_mode == self.DateMode.EXACT and self.depart_date and self.return_date:
            return self.depart_date, self.return_date

        if self.departure_date_from:
            base_depart = self.departure_date_from
        elif self.travel_month:
            base_depart = date(self.travel_month.year, self.travel_month.month, 15)
        else:
            base_depart = timezone.now().date() + timedelta(days=45)

        if self.departure_date_to and self.departure_date_to > base_depart:
            midpoint_days = max(0, int((self.departure_date_to - base_depart).days / 2))
            base_depart = base_depart + timedelta(days=midpoint_days)

        resolved_trip_min = max(1, int(self.trip_length_min or self.nights_min or 1))
        resolved_trip_max = max(resolved_trip_min, int(self.trip_length_max or self.nights_max or resolved_trip_min))
        avg_nights = max(resolved_trip_min, (resolved_trip_min + resolved_trip_max) // 2)
        base_return = base_depart + timedelta(days=avg_nights)
        return base_depart, base_return

    def resolve_departure_window(self) -> tuple[date, date]:
        if self.departure_date_from and self.departure_date_to:
            return self.departure_date_from, self.departure_date_to
        if self.depart_date:
            return self.depart_date, self.depart_date
        if self.travel_month:
            first = date(self.travel_month.year, self.travel_month.month, 1)
            return first, first + timedelta(days=27)
        today = timezone.now().date() + timedelta(days=30)
        return today, today + timedelta(days=7)


class DestinationCandidate(TimeStampedModel):
    plan = models.ForeignKey(PlanRequest, on_delete=models.CASCADE, related_name="destination_candidates")
    country_code = models.CharField(max_length=2)
    city_name = models.CharField(max_length=128)
    airport_code = models.CharField(max_length=8)
    latitude = models.FloatField(null=True, blank=True)
    longitude = models.FloatField(null=True, blank=True)
    timezone = models.CharField(max_length=64, blank=True)
    rank = models.PositiveSmallIntegerField(default=0)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        unique_together = ("plan", "airport_code")
        ordering = ["rank", "city_name"]

    def __str__(self) -> str:
        return f"{self.city_name} ({self.airport_code})"


class FlightOption(TimeStampedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    plan = models.ForeignKey(PlanRequest, on_delete=models.CASCADE, related_name="flight_options")
    candidate = models.ForeignKey(DestinationCandidate, on_delete=models.CASCADE, related_name="flight_options")
    provider = models.CharField(max_length=32)
    external_offer_id = models.CharField(max_length=128)
    origin_airport = models.CharField(max_length=8)
    destination_airport = models.CharField(max_length=8)
    departure_at = models.DateTimeField(null=True, blank=True)
    return_at = models.DateTimeField(null=True, blank=True)
    airline_codes = models.JSONField(default=list, blank=True)
    stops = models.PositiveSmallIntegerField(default=0)
    duration_minutes = models.PositiveIntegerField(default=0)
    cabin_class = models.CharField(max_length=32, default="economy")
    currency = models.CharField(max_length=3)
    total_price = models.DecimalField(max_digits=10, decimal_places=2)
    amount_minor = models.BigIntegerField(default=0)
    deeplink_url = models.TextField(blank=True)
    link_type = models.CharField(max_length=16, default="search")
    link_confidence = models.FloatField(default=0.7)
    link_rationale = models.CharField(max_length=255, blank=True)
    raw_payload = models.JSONField(default=dict, blank=True)
    last_checked_at = models.DateTimeField(default=timezone.now)

    class Meta:
        indexes = [
            models.Index(fields=["plan", "provider"]),
            models.Index(fields=["plan", "total_price"]),
        ]
        ordering = ["total_price"]

    def __str__(self) -> str:
        return f"Flight<{self.provider}:{self.external_offer_id}>"


class HotelOption(TimeStampedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    plan = models.ForeignKey(PlanRequest, on_delete=models.CASCADE, related_name="hotel_options")
    candidate = models.ForeignKey(DestinationCandidate, on_delete=models.CASCADE, related_name="hotel_options")
    provider = models.CharField(max_length=32)
    external_offer_id = models.CharField(max_length=128)
    provider_property_id = models.CharField(max_length=128, blank=True, db_index=True)
    name = models.CharField(max_length=255)
    star_rating = models.FloatField(default=0)
    guest_rating = models.FloatField(default=0)
    neighborhood = models.CharField(max_length=128, blank=True)
    latitude = models.FloatField(null=True, blank=True)
    longitude = models.FloatField(null=True, blank=True)
    amenities = models.JSONField(default=list, blank=True)
    currency = models.CharField(max_length=3)
    total_price = models.DecimalField(max_digits=10, decimal_places=2)
    amount_minor = models.BigIntegerField(default=0)
    deeplink_url = models.TextField(blank=True)
    link_type = models.CharField(max_length=16, default="search")
    link_confidence = models.FloatField(default=0.55)
    link_rationale = models.CharField(max_length=255, blank=True)
    raw_payload = models.JSONField(default=dict, blank=True)
    distance_km = models.FloatField(null=True, blank=True)
    last_checked_at = models.DateTimeField(default=timezone.now)

    class Meta:
        indexes = [
            models.Index(fields=["plan", "provider"]),
            models.Index(fields=["plan", "total_price"]),
        ]
        ordering = ["total_price"]

    def __str__(self) -> str:
        return f"Hotel<{self.provider}:{self.name}>"


class TourOption(TimeStampedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    plan = models.ForeignKey(PlanRequest, on_delete=models.CASCADE, related_name="tour_options")
    candidate = models.ForeignKey(DestinationCandidate, on_delete=models.CASCADE, related_name="tour_options")
    provider = models.CharField(max_length=32)
    external_product_id = models.CharField(max_length=128, db_index=True)
    name = models.CharField(max_length=255)
    currency = models.CharField(max_length=3, blank=True)
    total_price = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0"))
    amount_minor = models.BigIntegerField(default=0)
    deeplink_url = models.TextField(blank=True)
    link_type = models.CharField(max_length=16, default="search")
    link_confidence = models.FloatField(default=0.45)
    link_rationale = models.CharField(max_length=255, blank=True)
    raw_payload = models.JSONField(default=dict, blank=True)
    last_checked_at = models.DateTimeField(default=timezone.now)

    class Meta:
        indexes = [
            models.Index(fields=["plan", "provider"]),
            models.Index(fields=["plan", "candidate"]),
        ]
        ordering = ["total_price", "created_at"]

    def __str__(self) -> str:
        return f"Tour<{self.provider}:{self.external_product_id}>"


class PackageOption(TimeStampedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    plan = models.ForeignKey(PlanRequest, on_delete=models.CASCADE, related_name="package_options")
    candidate = models.ForeignKey(DestinationCandidate, on_delete=models.CASCADE, related_name="package_options")
    flight_option = models.ForeignKey(FlightOption, on_delete=models.CASCADE, related_name="package_options")
    hotel_option = models.ForeignKey(HotelOption, on_delete=models.CASCADE, related_name="package_options")
    tour_options = models.ManyToManyField(TourOption, blank=True, related_name="package_options")
    rank = models.PositiveSmallIntegerField(default=0)
    currency = models.CharField(max_length=3)
    total_price = models.DecimalField(max_digits=10, decimal_places=2)
    amount_minor = models.BigIntegerField(default=0)
    estimated_total_min = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0"))
    estimated_total_max = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0"))
    estimated_flight_min = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0"))
    estimated_flight_max = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0"))
    estimated_hotel_nightly_min = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0"))
    estimated_hotel_nightly_max = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0"))
    freshness_at = models.DateTimeField(default=timezone.now, db_index=True)
    flight_url = models.TextField(blank=True)
    hotel_url = models.TextField(blank=True)
    tours_url = models.TextField(blank=True)
    flight_entities = models.JSONField(default=list, blank=True)
    hotel_entities = models.JSONField(default=list, blank=True)
    tour_entities = models.JSONField(default=list, blank=True)
    place_entities = models.JSONField(default=list, blank=True)
    selected_tour_option_ids = models.JSONField(default=list, blank=True)
    price_breakdown = models.JSONField(default=dict, blank=True)
    component_links = models.JSONField(default=dict, blank=True)
    component_summary = models.JSONField(default=dict, blank=True)
    data_confidence = models.FloatField(default=0.75)
    score = models.FloatField(default=0)
    price_score = models.FloatField(default=0)
    convenience_score = models.FloatField(default=0)
    quality_score = models.FloatField(default=0)
    location_score = models.FloatField(default=0)
    explanations = models.JSONField(default=list, blank=True)
    score_breakdown = models.JSONField(default=dict, blank=True)
    last_scored_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["rank", "-score"]
        indexes = [
            models.Index(fields=["plan", "rank"]),
            models.Index(fields=["plan", "score"]),
        ]

    def __str__(self) -> str:
        return f"Package<{self.id}>"

    @property
    def price_age_seconds(self) -> int:
        if not self.last_scored_at:
            return 0
        delta = timezone.now() - self.last_scored_at
        return max(0, int(delta.total_seconds()))

    @property
    def package_total(self) -> Decimal:
        return self.total_price


class SavedPackage(TimeStampedModel):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="saved_packages")
    package = models.ForeignKey(PackageOption, on_delete=models.CASCADE, related_name="saved_by")

    class Meta:
        unique_together = ("user", "package")
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"SavedPackage<{self.user_id}:{self.package_id}>"


class SavedPlace(TimeStampedModel):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="saved_places")
    name = models.CharField(max_length=255)
    city = models.CharField(max_length=128, blank=True, null=True)
    country = models.CharField(max_length=128, blank=True, null=True)
    lat = models.FloatField(blank=True, null=True)
    lon = models.FloatField(blank=True, null=True)
    source = models.CharField(max_length=32, default="manual")
    external_id = models.CharField(max_length=255, blank=True, null=True, db_index=True)
    image_url = models.URLField(max_length=1500, blank=True, null=True)
    outbound_url = models.URLField(max_length=1500, blank=True, null=True)
    notes = models.TextField(blank=True, null=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "external_id"],
                condition=Q(external_id__isnull=False),
                name="planner_savedplace_user_external_id_unique",
            ),
            models.UniqueConstraint(
                fields=["user", "name", "city", "country"],
                condition=Q(external_id__isnull=True),
                name="planner_savedplace_user_name_city_country_unique",
            ),
        ]
        indexes = [
            models.Index(fields=["user", "created_at"]),
            models.Index(fields=["user", "source"]),
        ]

    def __str__(self) -> str:
        return f"SavedPlace<{self.user_id}:{self.name}>"

    @property
    def resolved_image_url(self) -> str:
        return str(self.image_url or "/static/img/destinations/travel-adventure-japan-night-landscape.jpg")

    def clean(self) -> None:
        for field in ("city", "country", "external_id", "image_url", "outbound_url", "notes"):
            value = getattr(self, field)
            if isinstance(value, str):
                value = value.strip()
                setattr(self, field, value or None)

        self.name = str(self.name or "").strip()
        if not self.name:
            raise ValidationError({"name": "Name is required."})

        if self.outbound_url and not is_allowed_outbound_url(self.outbound_url):
            raise ValidationError({"outbound_url": "Outbound URL is not allowed."})

        super().clean()


class ProviderError(TimeStampedModel):
    class ErrorType(models.TextChoices):
        TIMEOUT = "timeout", "Timeout"
        RATE_LIMIT = "rate_limit", "Rate limit"
        AUTH = "auth", "Auth"
        QUOTA = "quota", "Quota"
        PARSE = "parse", "Parse"
        EMPTY = "empty", "Empty"
        UNKNOWN = "unknown", "Unknown"

    plan = models.ForeignKey(PlanRequest, on_delete=models.CASCADE, related_name="provider_errors")
    provider = models.CharField(max_length=64)
    error_type = models.CharField(max_length=16, choices=ErrorType.choices, default=ErrorType.UNKNOWN)
    http_status = models.IntegerField(null=True, blank=True)
    provider_latency_ms = models.IntegerField(null=True, blank=True)
    context = models.CharField(max_length=128, blank=True)
    error_message = models.TextField()
    raw_payload = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["plan", "provider"]),
        ]

    def __str__(self) -> str:
        return f"ProviderError<{self.provider}>"


class ProviderCall(TimeStampedModel):
    provider = models.CharField(max_length=64, db_index=True)
    plan = models.ForeignKey(PlanRequest, null=True, blank=True, on_delete=models.SET_NULL, related_name="provider_calls")
    success = models.BooleanField(default=False)
    error_type = models.CharField(max_length=16, choices=ProviderError.ErrorType.choices, default=ProviderError.ErrorType.UNKNOWN)
    http_status = models.IntegerField(null=True, blank=True)
    latency_ms = models.IntegerField(null=True, blank=True)
    correlation_id = models.CharField(max_length=64, blank=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["provider", "created_at"]),
            models.Index(fields=["provider", "success", "created_at"]),
        ]

    def __str__(self) -> str:
        return f"ProviderCall<{self.provider}:{self.success}>"


class FxRate(TimeStampedModel):
    base_currency = models.CharField(max_length=3)
    quote_currency = models.CharField(max_length=3)
    rate = models.DecimalField(max_digits=18, decimal_places=8)
    as_of = models.DateTimeField(default=timezone.now)
    source = models.CharField(max_length=32, default="fallback")

    class Meta:
        unique_together = ("base_currency", "quote_currency")
        indexes = [
            models.Index(fields=["base_currency", "quote_currency"]),
            models.Index(fields=["as_of"]),
        ]
        ordering = ["-as_of"]

    def __str__(self) -> str:
        return f"FxRate<{self.base_currency}->{self.quote_currency}>"


class ClickEvent(TimeStampedModel):
    class LinkType(models.TextChoices):
        FLIGHT = "flight", "Flight"
        HOTEL = "hotel", "Hotel"
        TOUR = "tour", "Tour"
        PLACE = "place", "Place"
        OTHER = "other", "Other"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="click_events")
    plan = models.ForeignKey(PlanRequest, null=True, blank=True, on_delete=models.SET_NULL, related_name="click_events")
    package = models.ForeignKey(PackageOption, null=True, blank=True, on_delete=models.SET_NULL, related_name="click_events")
    provider = models.CharField(max_length=64)
    correlation_id = models.CharField(max_length=64, blank=True, db_index=True)
    link_type = models.CharField(max_length=16, choices=LinkType.choices, default=LinkType.OTHER, db_index=True)
    destination = models.CharField(max_length=128, blank=True)
    outbound_url = models.TextField(blank=True)
    url = models.TextField()
    clicked_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ["-clicked_at"]
        indexes = [
            models.Index(fields=["provider", "clicked_at"]),
            models.Index(fields=["plan", "clicked_at"]),
            models.Index(fields=["link_type", "clicked_at"]),
        ]

    def __str__(self) -> str:
        return f"ClickEvent<{self.provider}:{self.clicked_at}>"


class ConversionEvent(TimeStampedModel):
    click = models.ForeignKey(ClickEvent, null=True, blank=True, on_delete=models.SET_NULL, related_name="conversions")
    provider = models.CharField(max_length=64)
    external_conversion_id = models.CharField(max_length=128, blank=True)
    amount_minor = models.BigIntegerField(default=0)
    currency = models.CharField(max_length=3, default="USD")
    converted_at = models.DateTimeField(null=True, blank=True)
    notes = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["provider", "created_at"]),
        ]

    def __str__(self) -> str:
        return f"ConversionEvent<{self.provider}:{self.external_conversion_id}>"
