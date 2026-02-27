## .env.example
```
DJANGO_DEBUG=True
DJANGO_SECRET_KEY=change-me-in-production
DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1
DJANGO_CSRF_TRUSTED_ORIGINS=http://localhost:8000

POSTGRES_DB=trippilot
POSTGRES_USER=trippilot
POSTGRES_PASSWORD=trippilot
POSTGRES_HOST=postgres
POSTGRES_PORT=5432

REDIS_URL=redis://redis:6379/0
CELERY_BROKER_URL=redis://redis:6379/1
CELERY_RESULT_BACKEND=redis://redis:6379/2

SECURE_SSL_REDIRECT=False
SESSION_COOKIE_SECURE=False
CSRF_COOKIE_SECURE=False

# Links-only planner flags
TRIPPILOT_LINKS_ONLY=True
DEFAULT_ORIGIN_IATA=TBS

# Travelpayouts Data API (server-side token only)
TRAVELPAYOUTS_ENABLED=True
TRAVELPAYOUTS_API_TOKEN=
TRAVELPAYOUTS_MARKER=
TRAVELPAYOUTS_BASE_CURRENCY=USD

# Optional legacy providers (disabled when TRIPPILOT_LINKS_ONLY=True)
DUFFEL_ACCESS_TOKEN=
DUFFEL_BASE_URL=https://api.duffel.com
AMADEUS_CLIENT_ID=
AMADEUS_CLIENT_SECRET=
AMADEUS_BASE_URL=https://test.api.amadeus.com
EXPEDIA_RAPID_KEY=
EXPEDIA_RAPID_SECRET=
EXPEDIA_RAPID_BASE_URL=https://test.ean.com
EXPEDIA_RAPID_POS=US

# FX
FX_API_KEY=
FX_API_URL=https://api.freecurrencyapi.com/v1/latest
FX_QUOTE_CURRENCIES=USD,EUR,GBP,CAD

# Optional integrations
UNSPLASH_ACCESS_KEY=
SENTRY_DSN=

```

## README.md
```
# TriPPlanner (Links-Only Travel Planner)

TriPPlanner is a Django 5 + DRF + Celery + Redis + PostgreSQL app that ranks trip package estimates and returns outbound affiliate links only.

- No booking engine
- No checkout
- No reservations or payments in TriPPlanner

## Architecture

- `planner/`
  - API views/serializers/forms/models
  - Celery pipeline tasks (`planner/tasks.py`)
  - package scoring/building services
  - provider registry/health services
  - Travelpayouts integration module (`planner/services/travelpayouts/`)
- `trip_pilot/`
  - project settings/urls/celery/logging

Provider modules:
- Active: `planner/services/travelpayouts/client.py`, `planner/services/travelpayouts/adapter.py`
- Legacy (disabled when links-only flag is true): `planner/services/providers/duffel.py`, `planner/services/providers/amadeus.py`, `planner/services/providers/expedia_rapid.py`

## Core Behavior

`POST /api/plans/start` starts an async links-only pipeline:

1. Build destination candidates from bundled dataset
2. Fan-out by candidate and fetch Travelpayouts cached price/trend data
3. Fall back to deterministic baseline estimation when Travelpayouts is unavailable
4. Normalize currency (FX model + 1:1 fallback)
5. Score/rank and persist package estimates

`GET /api/plans/<plan_id>/packages` returns packages containing:

- destination (country/city/airport)
- estimated total min/max
- estimated flight min/max
- estimated hotel nightly min/max
- freshness timestamp
- deeplinks (`flight_url`, `hotel_url`, optional `tours_url`)
- score + score breakdown JSON

## Environment Variables

Copy env file:

- Windows: `copy .env.example .env`
- Mac/Linux: `cp .env.example .env`

Required for live Travelpayouts data:

- `TRAVELPAYOUTS_ENABLED=true`
- `TRAVELPAYOUTS_API_TOKEN=...`
- `TRAVELPAYOUTS_MARKER=...`
- `TRAVELPAYOUTS_BASE_CURRENCY=USD`
- `TRIPPILOT_LINKS_ONLY=true`
- `DEFAULT_ORIGIN_IATA=TBS` (optional)

FX and other optional integrations remain supported (`FX_API_KEY`, `UNSPLASH_ACCESS_KEY`, `SENTRY_DSN`).

## Run Commands

### Windows

1. `python -m pip install -r requirements.txt`
2. `python manage.py migrate`
3. `python manage.py runserver`
4. `celery -A trip_pilot worker --loglevel=info`
5. `celery -A trip_pilot beat --loglevel=info`

### Mac/Linux

1. `python3 -m pip install -r requirements.txt`
2. `python3 manage.py migrate`
3. `python3 manage.py runserver`
4. `celery -A trip_pilot worker --loglevel=info`
5. `celery -A trip_pilot beat --loglevel=info`

## Docker Compose

`docker compose up --build`

Services:

- `web`
- `worker`
- `beat`
- `postgres`
- `redis`

## API Endpoints

- `POST /api/plans/start`
- `POST /api/plans/<plan_id>/refresh`
- `GET /api/plans/<plan_id>/status`
- `GET /api/plans/<plan_id>/packages`
- `POST /api/click`
- `GET /api/providers/status`
- `GET /api/providers/health`

## Observability

- Structured JSON logs with request and plan correlation IDs
- Provider call/error metrics in DB
- `/api/providers/health` includes `travelpayouts` health fields:
  - `enabled`
  - `last_success_at`
  - `error_rate_1h`
  - `latency_p95`
  - `last_error_summary`

## Security

- Secrets read from environment variables only
- CSRF and cookie settings unchanged
- DRF throttling includes:
  - `plan_start`
  - `click_track`

```

## planner/admin.py
```
from django.contrib import admin

from planner.models import (
    ClickEvent,
    ConversionEvent,
    DestinationCandidate,
    FlightOption,
    FxRate,
    HotelOption,
    PackageOption,
    PlanRequest,
    Profile,
    ProviderCall,
    ProviderError,
    SavedPackage,
)


@admin.register(Profile)
class ProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "default_origin", "preferred_currency")
    search_fields = ("user__username", "user__email", "default_origin")


class DestinationCandidateInline(admin.TabularInline):
    model = DestinationCandidate
    extra = 0
    fields = ("rank", "city_name", "airport_code", "country_code", "metadata")
    readonly_fields = fields
    can_delete = False


class ProviderErrorInline(admin.TabularInline):
    model = ProviderError
    extra = 0
    fields = ("provider", "context", "error_message", "created_at")
    readonly_fields = fields
    can_delete = False


@admin.register(PlanRequest)
class PlanRequestAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "user",
        "origin_code",
        "destination_country",
        "status",
        "progress_percent",
        "created_at",
    )
    list_filter = ("status", "destination_country", "created_at")
    search_fields = ("id", "user__username", "origin_code", "destination_country")
    readonly_fields = (
        "id",
        "created_at",
        "updated_at",
        "started_at",
        "completed_at",
        "public_token",
    )
    inlines = [DestinationCandidateInline, ProviderErrorInline]


@admin.register(FlightOption)
class FlightOptionAdmin(admin.ModelAdmin):
    list_display = ("provider", "origin_airport", "destination_airport", "total_price", "currency", "amount_minor", "last_checked_at", "created_at")
    list_filter = ("provider", "currency")
    search_fields = ("external_offer_id", "plan__id", "origin_airport", "destination_airport")


@admin.register(HotelOption)
class HotelOptionAdmin(admin.ModelAdmin):
    list_display = (
        "provider",
        "name",
        "star_rating",
        "guest_rating",
        "distance_km",
        "total_price",
        "currency",
        "amount_minor",
        "last_checked_at",
    )
    list_filter = ("provider", "currency")
    search_fields = ("external_offer_id", "name", "plan__id")


@admin.register(PackageOption)
class PackageOptionAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "plan",
        "rank",
        "estimated_total_min",
        "estimated_total_max",
        "score",
        "currency",
        "freshness_at",
        "last_scored_at",
    )
    list_filter = ("currency",)
    search_fields = ("id", "plan__id")


@admin.register(SavedPackage)
class SavedPackageAdmin(admin.ModelAdmin):
    list_display = ("user", "package", "created_at")
    search_fields = ("user__username", "package__id")


@admin.register(ProviderError)
class ProviderErrorAdmin(admin.ModelAdmin):
    list_display = ("provider", "error_type", "http_status", "provider_latency_ms", "plan", "context", "created_at")
    search_fields = ("provider", "plan__id", "context")
    list_filter = ("provider", "created_at")


@admin.register(FxRate)
class FxRateAdmin(admin.ModelAdmin):
    list_display = ("base_currency", "quote_currency", "rate", "as_of", "source")
    search_fields = ("base_currency", "quote_currency", "source")
    list_filter = ("source", "as_of")


@admin.register(ProviderCall)
class ProviderCallAdmin(admin.ModelAdmin):
    list_display = ("provider", "success", "error_type", "http_status", "latency_ms", "correlation_id", "created_at")
    list_filter = ("provider", "success", "error_type", "created_at")
    search_fields = ("provider", "correlation_id", "plan__id")


@admin.register(ClickEvent)
class ClickEventAdmin(admin.ModelAdmin):
    list_display = ("provider", "link_type", "destination", "plan", "package", "user", "clicked_at")
    list_filter = ("provider", "link_type", "clicked_at")
    search_fields = ("provider", "destination", "correlation_id", "plan__id", "package__id", "user__username")


@admin.register(ConversionEvent)
class ConversionEventAdmin(admin.ModelAdmin):
    list_display = ("provider", "external_conversion_id", "amount_minor", "currency", "converted_at", "created_at")
    list_filter = ("provider", "currency", "created_at")
    search_fields = ("provider", "external_conversion_id")

```

## planner/api_views.py
```
from django.shortcuts import get_object_or_404
from django.urls import reverse
from rest_framework import permissions, serializers, status
from rest_framework.response import Response
from rest_framework.throttling import UserRateThrottle
from rest_framework.views import APIView

from planner.models import ClickEvent, PackageOption, PlanRequest, SavedPackage
from planner.serializers import PackageOptionSerializer, PlanStartSerializer, PlanStatusSerializer
from planner.services.provider_health import provider_health_payload
from planner.services.provider_registry import provider_status
from planner.services.plan_service import create_plan_request
from planner.tasks import refresh_top_packages_task


class PlanStartThrottle(UserRateThrottle):
    scope = "plan_start"


class ClickTrackThrottle(UserRateThrottle):
    scope = "click_track"


class ClickEventSerializer(serializers.Serializer):
    plan_id = serializers.UUIDField(required=False, allow_null=True)
    package_id = serializers.UUIDField(required=False, allow_null=True)
    provider = serializers.CharField(max_length=64, required=False, allow_blank=True, default="travelpayouts")
    link_type = serializers.ChoiceField(
        choices=("flight", "hotel", "tour", "other"),
        required=False,
        default="other",
    )
    destination = serializers.CharField(max_length=128, required=False, allow_blank=True, default="")
    correlation_id = serializers.CharField(max_length=128, required=False, allow_blank=True, default="")
    outbound_url = serializers.URLField(max_length=1500, required=False, allow_null=True)
    url = serializers.URLField(max_length=1500, required=False, allow_null=True)


class PlanStartAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [PlanStartThrottle]

    def post(self, request):  # noqa: ANN001, ANN201
        serializer = PlanStartSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        plan = create_plan_request(request.user, serializer.validated_data)
        return Response(
            {
                "plan_id": str(plan.id),
                "status": plan.status,
                "status_url": request.build_absolute_uri(reverse("planner-api:plan-status", kwargs={"plan_id": plan.id})),
                "results_url": request.build_absolute_uri(reverse("planner:results", kwargs={"plan_id": plan.id})),
                "share_url": request.build_absolute_uri(reverse("planner:share", kwargs={"token": plan.public_token})),
            },
            status=status.HTTP_202_ACCEPTED,
        )


class PlanRefreshAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, plan_id):  # noqa: ANN001, ANN201
        plan = get_object_or_404(PlanRequest, pk=plan_id, user=request.user)
        task = refresh_top_packages_task.delay(str(plan.id), 5)
        return Response(
            {
                "plan_id": str(plan.id),
                "refresh_task_id": task.id,
                "message": "Refresh queued for top packages.",
            },
            status=status.HTTP_202_ACCEPTED,
        )


class PlanStatusAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, plan_id):  # noqa: ANN001, ANN201
        plan = get_object_or_404(PlanRequest, pk=plan_id, user=request.user)
        serializer = PlanStatusSerializer(plan)
        payload = serializer.data
        flags = provider_status()
        payload["fx_configured"] = flags.get("fx_enabled", False)
        payload["links_only_enabled"] = flags.get("links_only_enabled", True)
        return Response(payload)


class PlanPackagesAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, plan_id):  # noqa: ANN001, ANN201
        plan = get_object_or_404(PlanRequest, pk=plan_id, user=request.user)
        sort_mode = request.query_params.get("sort", "best_value")
        queryset = plan.package_options.select_related("flight_option", "hotel_option", "candidate")
        if sort_mode == "cheapest":
            queryset = queryset.order_by("estimated_total_min", "-score")
        elif sort_mode == "fastest":
            queryset = queryset.order_by("-convenience_score", "estimated_total_min")
        elif sort_mode == "best_hotel":
            queryset = queryset.order_by("-quality_score", "estimated_total_min")
        else:
            queryset = queryset.order_by("-score", "estimated_total_min")
        serializer = PackageOptionSerializer(queryset, many=True, context={"request": request})
        return Response(serializer.data)


class PackageSaveToggleAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, package_id):  # noqa: ANN001, ANN201
        package = get_object_or_404(PackageOption, pk=package_id, plan__user=request.user)
        item, created = SavedPackage.objects.get_or_create(user=request.user, package=package)
        if not created:
            item.delete()
        return Response({"saved": created}, status=status.HTTP_200_OK)


class ClickTrackingAPIView(APIView):
    permission_classes = [permissions.AllowAny]
    throttle_classes = [ClickTrackThrottle]

    def post(self, request):  # noqa: ANN001, ANN201
        serializer = ClickEventSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        plan = None
        package = None
        if serializer.validated_data.get("plan_id"):
            plan = PlanRequest.objects.filter(pk=serializer.validated_data["plan_id"]).first()
        if serializer.validated_data.get("package_id"):
            package = PackageOption.objects.filter(pk=serializer.validated_data["package_id"]).first()

        outbound_url = serializer.validated_data.get("outbound_url") or serializer.validated_data.get("url")
        if not outbound_url:
            raise serializers.ValidationError({"outbound_url": "outbound_url or url is required."})

        ClickEvent.objects.create(
            user=request.user if request.user.is_authenticated else None,
            plan=plan,
            package=package,
            provider=serializer.validated_data.get("provider") or "travelpayouts",
            link_type=serializer.validated_data.get("link_type") or "other",
            destination=serializer.validated_data.get("destination", ""),
            correlation_id=serializer.validated_data.get("correlation_id", "")[:64],
            outbound_url=outbound_url,
            url=outbound_url,
        )
        return Response({"tracked": True}, status=status.HTTP_201_CREATED)


class ProviderStatusAPIView(APIView):
    permission_classes = [permissions.AllowAny]

    def get(self, request):  # noqa: ANN001, ANN201
        return Response(provider_status())


class ProviderHealthAPIView(APIView):
    permission_classes = [permissions.AllowAny]

    def get(self, request):  # noqa: ANN001, ANN201
        return Response(provider_health_payload())

```

## planner/forms.py
```
from datetime import date

from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User

from planner.models import PlanRequest, Profile


class SignUpForm(UserCreationForm):
    email = forms.EmailField(required=True)

    class Meta:
        model = User
        fields = ("username", "email", "password1", "password2")


class ProfileForm(forms.ModelForm):
    travel_preferences = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 4}),
        help_text="Short JSON object or free-form text. Example: nonstop, boutique hotels, walkable areas.",
    )

    class Meta:
        model = Profile
        fields = (
            "default_origin",
            "preferred_currency",
            "default_budget_min",
            "default_budget_max",
            "travel_preferences",
        )


class PlannerWizardForm(forms.Form):
    DATE_MODE_CHOICES = (
        (PlanRequest.DateMode.EXACT, "Exact dates"),
        (PlanRequest.DateMode.FLEXIBLE, "Month + flexibility"),
    )
    CABIN_CHOICES = (
        ("economy", "Economy"),
        ("premium_economy", "Premium Economy"),
        ("business", "Business"),
        ("first", "First"),
    )
    PREFERENCE_CHOICES = (
        ("beach", "Beach"),
        ("nature", "Nature"),
        ("culture", "Culture"),
        ("nightlife", "Nightlife"),
        ("food", "Food"),
        ("quiet", "Quiet"),
    )

    origin_input = forms.CharField(max_length=64)
    destination_country = forms.CharField(max_length=2)
    date_mode = forms.ChoiceField(choices=DATE_MODE_CHOICES, initial=PlanRequest.DateMode.EXACT)
    depart_date = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}))
    return_date = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}))
    travel_month = forms.DateField(
        required=False,
        input_formats=["%Y-%m", "%Y-%m-%d"],
        widget=forms.DateInput(attrs={"type": "month"}),
    )
    flexibility_days = forms.IntegerField(min_value=0, max_value=14, initial=2, required=False)
    nights_min = forms.IntegerField(min_value=1, max_value=30, initial=4)
    nights_max = forms.IntegerField(min_value=1, max_value=45, initial=8)
    total_budget = forms.DecimalField(min_value=200, max_digits=10, decimal_places=2, initial=1800)
    travelers = forms.IntegerField(min_value=1, max_value=9, initial=2)
    currency = forms.CharField(max_length=3, initial="USD")
    hotel_stars_min = forms.IntegerField(min_value=1, max_value=5, initial=3, required=False)
    hotel_guest_rating_min = forms.FloatField(min_value=0, max_value=10, initial=7.5, required=False)
    hotel_amenities = forms.MultipleChoiceField(
        required=False,
        choices=(
            ("wifi", "Wi-Fi"),
            ("pool", "Pool"),
            ("parking", "Parking"),
            ("spa", "Spa"),
            ("breakfast", "Breakfast"),
            ("gym", "Gym"),
        ),
    )
    flight_max_stops = forms.IntegerField(min_value=0, max_value=3, initial=1, required=False)
    flight_max_duration_minutes = forms.IntegerField(min_value=60, max_value=2880, initial=1200, required=False)
    cabin = forms.ChoiceField(choices=CABIN_CHOICES, initial="economy")
    preferences = forms.MultipleChoiceField(required=False, choices=PREFERENCE_CHOICES)

    def clean_destination_country(self) -> str:
        value = self.cleaned_data["destination_country"].upper().strip()
        if len(value) != 2:
            raise forms.ValidationError("Destination country must be an ISO-2 code.")
        return value

    def clean_origin_input(self) -> str:
        value = self.cleaned_data["origin_input"].upper().strip()
        if len(value) < 3:
            raise forms.ValidationError("Origin must be an airport code or city.")
        return value

    def clean(self):  # noqa: ANN201
        cleaned = super().clean()
        date_mode = cleaned.get("date_mode")
        depart_date = cleaned.get("depart_date")
        return_date = cleaned.get("return_date")
        travel_month = cleaned.get("travel_month")

        if date_mode == PlanRequest.DateMode.EXACT:
            if not depart_date or not return_date:
                self.add_error("depart_date", "Departure and return dates are required for exact mode.")
            elif return_date <= depart_date:
                self.add_error("return_date", "Return date must be after departure date.")
        else:
            if not travel_month:
                self.add_error("travel_month", "Choose a month for flexible mode.")
            elif travel_month < date(date.today().year, date.today().month, 1):
                self.add_error("travel_month", "Travel month must be current or future.")

        nights_min = cleaned.get("nights_min")
        nights_max = cleaned.get("nights_max")
        if nights_min and nights_max and nights_max < nights_min:
            self.add_error("nights_max", "Max nights must be greater than or equal to min nights.")

        return cleaned

    def to_plan_payload(self) -> dict:
        return {
            "origin_input": self.cleaned_data["origin_input"],
            "destination_country": self.cleaned_data["destination_country"],
            "date_mode": self.cleaned_data["date_mode"],
            "depart_date": self.cleaned_data.get("depart_date"),
            "return_date": self.cleaned_data.get("return_date"),
            "travel_month": self.cleaned_data.get("travel_month"),
            "flexibility_days": self.cleaned_data.get("flexibility_days") or 0,
            "nights_min": self.cleaned_data["nights_min"],
            "nights_max": self.cleaned_data["nights_max"],
            "total_budget": self.cleaned_data["total_budget"],
            "travelers": self.cleaned_data["travelers"],
            "search_currency": self.cleaned_data["currency"].upper(),
            "hotel_filters": {
                "stars_min": self.cleaned_data.get("hotel_stars_min"),
                "guest_rating_min": self.cleaned_data.get("hotel_guest_rating_min"),
                "amenities": self.cleaned_data.get("hotel_amenities", []),
            },
            "flight_filters": {
                "max_stops": self.cleaned_data.get("flight_max_stops"),
                "max_duration_minutes": self.cleaned_data.get("flight_max_duration_minutes"),
                "cabin": self.cleaned_data.get("cabin"),
            },
            "preferences": {
                key: 1.0 for key in self.cleaned_data.get("preferences", [])
            },
        }

```

## planner/models.py
```
import uuid
from datetime import date, timedelta
from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils import timezone


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


class PlanRequest(TimeStampedModel):
    class DateMode(models.TextChoices):
        EXACT = "exact", "Exact dates"
        FLEXIBLE = "flexible", "Month and flexibility"

    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        SEARCHING_FLIGHTS = "searching_flights", "Searching flights"
        SEARCHING_HOTELS = "searching_hotels", "Searching hotels"
        BUILDING_PACKAGES = "building_packages", "Building packages"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="plan_requests")
    origin_input = models.CharField(max_length=64)
    origin_code = models.CharField(max_length=8)
    destination_country = models.CharField(max_length=2, db_index=True)
    date_mode = models.CharField(max_length=16, choices=DateMode.choices, default=DateMode.EXACT)
    depart_date = models.DateField(null=True, blank=True)
    return_date = models.DateField(null=True, blank=True)
    travel_month = models.DateField(null=True, blank=True)
    flexibility_days = models.PositiveSmallIntegerField(default=0)
    nights_min = models.PositiveSmallIntegerField(default=3)
    nights_max = models.PositiveSmallIntegerField(default=7)
    total_budget = models.DecimalField(max_digits=10, decimal_places=2)
    travelers = models.PositiveSmallIntegerField(default=1)
    search_currency = models.CharField(max_length=3, default="USD")
    hotel_filters = models.JSONField(default=dict, blank=True)
    flight_filters = models.JSONField(default=dict, blank=True)
    preference_weights = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=32, choices=Status.choices, default=Status.QUEUED, db_index=True)
    progress_message = models.CharField(max_length=255, blank=True)
    progress_percent = models.PositiveSmallIntegerField(default=0)
    error_message = models.TextField(blank=True)
    public_token = models.CharField(max_length=32, unique=True, default=token_hex, db_index=True)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"PlanRequest<{self.id}>"

    def resolve_dates(self) -> tuple[date, date]:
        if self.date_mode == self.DateMode.EXACT and self.depart_date and self.return_date:
            return self.depart_date, self.return_date

        if self.travel_month:
            base_depart = date(self.travel_month.year, self.travel_month.month, 15)
        else:
            base_depart = timezone.now().date() + timedelta(days=45)

        avg_nights = max(self.nights_min, (self.nights_min + self.nights_max) // 2)
        base_return = base_depart + timedelta(days=avg_nights)
        return base_depart, base_return


class DestinationCandidate(TimeStampedModel):
    plan = models.ForeignKey(PlanRequest, on_delete=models.CASCADE, related_name="destination_candidates")
    country_code = models.CharField(max_length=2)
    city_name = models.CharField(max_length=128)
    airport_code = models.CharField(max_length=8)
    latitude = models.FloatField(null=True, blank=True)
    longitude = models.FloatField(null=True, blank=True)
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


class PackageOption(TimeStampedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    plan = models.ForeignKey(PlanRequest, on_delete=models.CASCADE, related_name="package_options")
    candidate = models.ForeignKey(DestinationCandidate, on_delete=models.CASCADE, related_name="package_options")
    flight_option = models.ForeignKey(FlightOption, on_delete=models.CASCADE, related_name="package_options")
    hotel_option = models.ForeignKey(HotelOption, on_delete=models.CASCADE, related_name="package_options")
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


class SavedPackage(TimeStampedModel):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="saved_packages")
    package = models.ForeignKey(PackageOption, on_delete=models.CASCADE, related_name="saved_by")

    class Meta:
        unique_together = ("user", "package")
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"SavedPackage<{self.user_id}:{self.package_id}>"


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

```

## planner/serializers.py
```
from __future__ import annotations

from rest_framework import serializers

from planner.models import FlightOption, HotelOption, PackageOption, PlanRequest, SavedPackage

PREFERENCE_KEYS = ("beach", "nature", "culture", "nightlife", "food", "quiet")


class PlanStartSerializer(serializers.Serializer):
    origin_input = serializers.CharField(max_length=64)
    destination_country = serializers.CharField(max_length=2)
    date_mode = serializers.ChoiceField(choices=PlanRequest.DateMode.values)
    depart_date = serializers.DateField(required=False, allow_null=True)
    return_date = serializers.DateField(required=False, allow_null=True)
    travel_month = serializers.DateField(required=False, allow_null=True)
    flexibility_days = serializers.IntegerField(min_value=0, max_value=14, required=False, default=0)
    nights_min = serializers.IntegerField(min_value=1, max_value=30)
    nights_max = serializers.IntegerField(min_value=1, max_value=45)
    total_budget = serializers.DecimalField(max_digits=10, decimal_places=2, min_value=200)
    travelers = serializers.IntegerField(min_value=1, max_value=9)
    search_currency = serializers.CharField(max_length=3, default="USD")
    hotel_filters = serializers.JSONField(required=False, default=dict)
    flight_filters = serializers.JSONField(required=False, default=dict)
    preferences = serializers.JSONField(required=False, default=dict)

    def validate_destination_country(self, value: str) -> str:
        value = value.upper().strip()
        if len(value) != 2:
            raise serializers.ValidationError("Destination country must be ISO-2.")
        return value

    def validate_origin_input(self, value: str) -> str:
        value = value.upper().strip()
        if len(value) < 3:
            raise serializers.ValidationError("Origin must be a city or IATA code.")
        return value

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

    def validate(self, attrs):  # noqa: ANN201
        date_mode = attrs["date_mode"]
        if date_mode == PlanRequest.DateMode.EXACT:
            if not attrs.get("depart_date") or not attrs.get("return_date"):
                raise serializers.ValidationError("depart_date and return_date are required for exact mode.")
            if attrs["return_date"] <= attrs["depart_date"]:
                raise serializers.ValidationError("return_date must be after depart_date.")
        else:
            if not attrs.get("travel_month"):
                raise serializers.ValidationError("travel_month is required for flexible mode.")
        if attrs["nights_max"] < attrs["nights_min"]:
            raise serializers.ValidationError("nights_max must be >= nights_min.")
        attrs["search_currency"] = attrs["search_currency"].upper()
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
            "cabin_class",
            "currency",
            "total_price",
            "amount_minor",
            "last_checked_at",
            "deeplink_url",
        )


class HotelOptionSerializer(serializers.ModelSerializer):
    class Meta:
        model = HotelOption
        fields = (
            "id",
            "provider",
            "external_offer_id",
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
        )


class PackageOptionSerializer(serializers.ModelSerializer):
    flight = FlightOptionSerializer(source="flight_option")
    hotel = HotelOptionSerializer(source="hotel_option")
    destination = serializers.SerializerMethodField()
    deeplinks = serializers.SerializerMethodField()
    freshness_timestamp = serializers.DateTimeField(source="freshness_at", read_only=True)
    estimated_hotel_min = serializers.DecimalField(max_digits=10, decimal_places=2, source="estimated_hotel_nightly_min", read_only=True)
    estimated_hotel_max = serializers.DecimalField(max_digits=10, decimal_places=2, source="estimated_hotel_nightly_max", read_only=True)
    saved = serializers.SerializerMethodField()
    price_age_seconds = serializers.IntegerField(read_only=True)

    class Meta:
        model = PackageOption
        fields = (
            "id",
            "rank",
            "destination",
            "currency",
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
            "saved",
        )

    def get_destination(self, obj: PackageOption) -> dict[str, str]:
        return {
            "country": obj.candidate.country_code,
            "city": obj.candidate.city_name,
            "airport": obj.candidate.airport_code,
        }

    def get_deeplinks(self, obj: PackageOption) -> dict[str, str | None]:
        return {
            "flight_url": obj.flight_url or obj.flight_option.deeplink_url,
            "hotel_url": obj.hotel_url or obj.hotel_option.deeplink_url,
            "tours_url": obj.tours_url or None,
        }

    def get_saved(self, obj: PackageOption) -> bool:
        request = self.context.get("request")
        user = getattr(request, "user", None) if request else None
        if not user or not user.is_authenticated:
            return False
        return SavedPackage.objects.filter(user=user, package=obj).exists()

```

## planner/services/config.py
```
import os


def env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def links_only_enabled() -> bool:
    return env_bool("TRIPPILOT_LINKS_ONLY", default=True)


def travelpayouts_enabled() -> bool:
    return env_bool("TRAVELPAYOUTS_ENABLED", default=True)


def travelpayouts_base_currency() -> str:
    return os.getenv("TRAVELPAYOUTS_BASE_CURRENCY", "USD").upper().strip() or "USD"


def travelpayouts_marker() -> str:
    return (
        os.getenv("TRAVELPAYOUTS_MARKER", "").strip()
        or os.getenv("TRIPPILOT_AFFILIATE_ID", "").strip()
    )


def default_origin_iata() -> str:
    return os.getenv("DEFAULT_ORIGIN_IATA", "").strip().upper()


def travelpayouts_api_token() -> str:
    return os.getenv("TRAVELPAYOUTS_API_TOKEN", "").strip()

```

## planner/services/deeplinks.py
```
from __future__ import annotations

from datetime import date
from urllib.parse import parse_qsl, quote_plus, urlencode, urlparse, urlunparse

from planner.services.config import travelpayouts_marker


def _merge_query(url: str, extra_params: dict[str, str]) -> str:
    parsed = urlparse(url)
    existing = dict(parse_qsl(parsed.query, keep_blank_values=True))
    existing.update({k: v for k, v in extra_params.items() if v not in (None, "")})
    query = urlencode(existing, doseq=True)
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, query, parsed.fragment))


def _tracking_params(
    *,
    provider: str,
    plan_id: str | None,
    package_id: str | None,
    link_type: str | None,
    destination: str | None,
) -> dict[str, str]:
    params = {
        "utm_source": "trippilot",
        "utm_medium": "affiliate",
        "utm_campaign": "country_planner",
        "tp_provider": provider,
    }
    marker = travelpayouts_marker()
    if marker:
        params["affiliate_id"] = marker
        params["marker"] = marker
    if plan_id:
        params["tp_plan"] = str(plan_id)
    if package_id:
        params["tp_package"] = str(package_id)
    if link_type:
        params["tp_link_type"] = link_type
    if destination:
        params["tp_destination"] = destination
    return params


def build_tracked_deeplink(
    url: str,
    *,
    provider: str,
    plan_id: str | None = None,
    package_id: str | None = None,
    link_type: str | None = None,
    destination: str | None = None,
) -> str:
    if not url:
        return url
    return _merge_query(
        url,
        _tracking_params(
            provider=provider,
            plan_id=plan_id,
            package_id=package_id,
            link_type=link_type,
            destination=destination,
        ),
    )


def build_flight_search_link(
    *,
    origin: str,
    destination: str,
    depart_date: date,
    return_date: date | None,
    travelers: int,
    cabin: str = "economy",
    plan_id: str | None = None,
    package_id: str | None = None,
    destination_label: str | None = None,
) -> str:
    base_url = "https://www.aviasales.com/search"
    params = {
        "origin": origin,
        "destination": destination,
        "depart_date": depart_date.isoformat(),
        "adults": max(1, int(travelers)),
        "cabin": cabin,
    }
    if return_date:
        params["return_date"] = return_date.isoformat()
    raw_url = _merge_query(base_url, params)
    return build_tracked_deeplink(
        raw_url,
        provider="travelpayouts",
        plan_id=plan_id,
        package_id=package_id,
        link_type="flight",
        destination=destination_label,
    )


def build_hotel_search_link(
    *,
    city: str,
    country_code: str,
    checkin: date,
    checkout: date,
    adults: int,
    plan_id: str | None = None,
    package_id: str | None = None,
) -> str:
    base_url = "https://www.booking.com/searchresults.html"
    marker = travelpayouts_marker()
    params = {
        "ss": f"{city}, {country_code}",
        "checkin": checkin.isoformat(),
        "checkout": checkout.isoformat(),
        "group_adults": max(1, int(adults)),
        "no_rooms": 1,
    }
    if marker:
        params["aid"] = marker
    raw_url = _merge_query(base_url, params)
    return build_tracked_deeplink(
        raw_url,
        provider="travelpayouts",
        plan_id=plan_id,
        package_id=package_id,
        link_type="hotel",
        destination=f"{city}-{country_code}",
    )


def build_tour_search_link(
    *,
    city: str,
    country_code: str,
    plan_id: str | None = None,
    package_id: str | None = None,
) -> str:
    query = quote_plus(f"{city} {country_code}")
    raw_url = f"https://www.getyourguide.com/s/?q={query}"
    return build_tracked_deeplink(
        raw_url,
        provider="travelpayouts",
        plan_id=plan_id,
        package_id=package_id,
        link_type="tour",
        destination=f"{city}-{country_code}",
    )


def affiliate_configured() -> bool:
    return bool(travelpayouts_marker())

```

## planner/services/destination_service.py
```
import json
from functools import lru_cache
from pathlib import Path

from planner.models import DestinationCandidate, PlanRequest
from planner.services.travelpayouts.fallbacks import (
    airport_override_profile,
    country_default_profile,
)


DATA_PATH = Path(__file__).resolve().parents[1] / "data" / "country_destinations.json"


@lru_cache(maxsize=1)
def load_country_dataset() -> dict:
    with DATA_PATH.open("r", encoding="utf-8") as file:
        return json.load(file)


@lru_cache(maxsize=512)
def airport_coordinates(airport_code: str) -> tuple[float, float] | None:
    probe = airport_code.strip().upper()
    if not probe:
        return None
    dataset = load_country_dataset()
    for country_data in dataset.values():
        for item in country_data.get("destinations", []):
            if str(item.get("airport", "")).upper() == probe:
                lat = item.get("lat")
                lng = item.get("lng")
                if lat is None or lng is None:
                    return None
                return float(lat), float(lng)
    return None


def resolve_origin_code(origin_input: str) -> str:
    raw = origin_input.strip().upper()
    if len(raw) == 3 and raw.isalpha():
        return raw

    dataset = load_country_dataset()
    normalized_city = raw.replace(",", " ").split()[0]
    for country_data in dataset.values():
        for item in country_data.get("destinations", []):
            city = item.get("city", "").upper()
            if normalized_city and normalized_city in city:
                return item.get("airport", raw[:3])
    return raw[:3]


def build_destination_candidates(plan: PlanRequest, max_items: int = 6) -> list[DestinationCandidate]:
    dataset = load_country_dataset()
    country_code = plan.destination_country.upper()
    country_data = dataset.get(country_code, {})
    destinations = country_data.get("destinations", [])[:max_items]

    plan.destination_candidates.all().delete()
    candidates: list[DestinationCandidate] = []
    country_defaults = country_default_profile(country_code)
    for idx, item in enumerate(destinations, start=1):
        airport_code = str(item["airport"]).upper()
        override = airport_override_profile(airport_code)
        metadata = {
            "tier": override.get("tier") or item.get("tier") or country_defaults.get("tier", "standard"),
            "tags": list(dict.fromkeys(override.get("tags") or item.get("tags") or country_defaults.get("tags", []))),
            "nonstop_likelihood": float(
                override.get("nonstop_likelihood")
                or item.get("nonstop_likelihood")
                or country_defaults.get("nonstop_likelihood", 0.55),
            ),
        }
        candidates.append(
            DestinationCandidate(
                plan=plan,
                country_code=country_code,
                city_name=item["city"],
                airport_code=airport_code,
                latitude=item.get("lat"),
                longitude=item.get("lng"),
                rank=idx,
                metadata=metadata,
            ),
        )

    created = DestinationCandidate.objects.bulk_create(candidates)
    return created

```

## planner/services/package_builder.py
```
from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

from django.utils import timezone

from planner.models import FlightOption, HotelOption, PackageOption, PlanRequest
from planner.services.deeplinks import build_tour_search_link
from planner.services.fx import convert_decimal, to_minor_units
from planner.services.scoring import score_package


def _sort_key(sort_mode: str):
    if sort_mode == "cheapest":
        return lambda item: (item["estimated_total_min"], -item["score"])
    if sort_mode == "fastest":
        return lambda item: (-item["convenience_score"], item["estimated_total_min"])
    if sort_mode == "best_hotel":
        return lambda item: (-item["quality_score"], item["estimated_total_min"])
    return lambda item: (-item["score"], item["estimated_total_min"])


def _as_decimal(raw: dict, key: str, fallback: Decimal) -> Decimal:
    value = raw.get(key)
    if value in (None, ""):
        return fallback
    try:
        return Decimal(str(value)).quantize(Decimal("0.01"))
    except Exception:  # noqa: BLE001
        return fallback


def build_packages_for_plan(
    plan: PlanRequest,
    sort_mode: str = "best_value",
    max_packages: int = 10,
    flights_per_city: int = 3,
    hotels_per_city: int = 3,
) -> list[PackageOption]:
    PackageOption.objects.filter(plan=plan).delete()
    target_currency = (plan.search_currency or "USD").upper()

    flights_by_candidate: dict[int, list[FlightOption]] = defaultdict(list)
    hotels_by_candidate: dict[int, list[HotelOption]] = defaultdict(list)

    for flight in plan.flight_options.select_related("candidate").order_by("total_price"):
        flights_by_candidate[flight.candidate_id].append(flight)

    for hotel in plan.hotel_options.select_related("candidate").order_by("total_price"):
        hotels_by_candidate[hotel.candidate_id].append(hotel)

    nights_low = max(1, int(plan.nights_min or 1))
    nights_high = max(nights_low, int(plan.nights_max or nights_low))
    budget_minor = to_minor_units(plan.total_budget)
    preferences = plan.preference_weights or {}

    combinations: list[dict] = []
    for candidate in plan.destination_candidates.all():
        flights = flights_by_candidate.get(candidate.id, [])[:flights_per_city]
        hotels = hotels_by_candidate.get(candidate.id, [])[:hotels_per_city]
        if not flights or not hotels:
            continue

        tags = [str(tag).lower() for tag in (candidate.metadata or {}).get("tags", [])]
        for flight in flights:
            flight_raw = flight.raw_payload or {}
            flight_min = _as_decimal(flight_raw, "estimated_min", flight.total_price)
            flight_max = _as_decimal(flight_raw, "estimated_max", flight.total_price)

            for hotel in hotels:
                hotel_raw = hotel.raw_payload or {}
                hotel_nightly_min = _as_decimal(hotel_raw, "nightly_min", hotel.total_price / max(nights_low, 1))
                hotel_nightly_max = _as_decimal(hotel_raw, "nightly_max", hotel.total_price / max(nights_high, 1))

                est_flight_min = convert_decimal(flight_min, flight.currency, target_currency)
                est_flight_max = convert_decimal(flight_max, flight.currency, target_currency)
                est_hotel_min = convert_decimal(hotel_nightly_min, hotel.currency, target_currency)
                est_hotel_max = convert_decimal(hotel_nightly_max, hotel.currency, target_currency)

                est_total_min = (est_flight_min + (est_hotel_min * nights_low)).quantize(Decimal("0.01"))
                est_total_max = (est_flight_max + (est_hotel_max * nights_high)).quantize(Decimal("0.01"))
                midpoint_total = ((est_total_min + est_total_max) / Decimal("2")).quantize(Decimal("0.01"))

                freshness_candidates = [value for value in [flight.last_checked_at, hotel.last_checked_at] if value]
                freshness_at = min(freshness_candidates) if freshness_candidates else timezone.now()

                distance_band = str(
                    flight_raw.get("distance_band")
                    or hotel_raw.get("distance_band")
                    or (candidate.metadata or {}).get("distance_band")
                    or "medium"
                )
                nonstop_likelihood = float(
                    flight_raw.get("nonstop_likelihood")
                    or (candidate.metadata or {}).get("nonstop_likelihood")
                    or 0.55,
                )
                season_multiplier = float(
                    flight_raw.get("season_multiplier")
                    or hotel_raw.get("season_multiplier")
                    or 1.0,
                )

                score = score_package(
                    total_minor=to_minor_units(midpoint_total),
                    budget_minor=budget_minor,
                    preference_weights=preferences,
                    candidate_tags=tags,
                    season_multiplier=season_multiplier,
                    distance_band=distance_band,
                    nonstop_likelihood=nonstop_likelihood,
                    freshness_at=freshness_at,
                )

                breakdown = score.breakdown.copy()
                breakdown["distance_band"] = distance_band
                breakdown["season_multiplier"] = season_multiplier
                breakdown["freshness_timestamp"] = freshness_at.isoformat()
                breakdown["source"] = flight_raw.get("data_source") or hotel_raw.get("data_source") or "fallback"

                combinations.append(
                    {
                        "candidate": candidate,
                        "flight": flight,
                        "hotel": hotel,
                        "estimated_flight_min": est_flight_min,
                        "estimated_flight_max": est_flight_max,
                        "estimated_hotel_nightly_min": est_hotel_min,
                        "estimated_hotel_nightly_max": est_hotel_max,
                        "estimated_total_min": est_total_min,
                        "estimated_total_max": est_total_max,
                        "midpoint_total": midpoint_total,
                        "freshness_at": freshness_at,
                        "score": score.score,
                        "price_score": score.price_score,
                        "convenience_score": score.convenience_score,
                        "quality_score": score.quality_score,
                        "location_score": score.location_score,
                        "explanations": score.explanations,
                        "score_breakdown": breakdown,
                    },
                )

    if not combinations:
        return []

    combinations.sort(key=_sort_key(sort_mode))
    selected = combinations[:max_packages]

    records: list[PackageOption] = []
    for idx, item in enumerate(selected, start=1):
        candidate = item["candidate"]
        records.append(
            PackageOption(
                plan=plan,
                candidate=candidate,
                flight_option=item["flight"],
                hotel_option=item["hotel"],
                rank=idx,
                currency=target_currency,
                total_price=item["midpoint_total"],
                amount_minor=to_minor_units(item["midpoint_total"]),
                estimated_total_min=item["estimated_total_min"],
                estimated_total_max=item["estimated_total_max"],
                estimated_flight_min=item["estimated_flight_min"],
                estimated_flight_max=item["estimated_flight_max"],
                estimated_hotel_nightly_min=item["estimated_hotel_nightly_min"],
                estimated_hotel_nightly_max=item["estimated_hotel_nightly_max"],
                freshness_at=item["freshness_at"],
                flight_url=item["flight"].deeplink_url,
                hotel_url=item["hotel"].deeplink_url,
                tours_url=build_tour_search_link(
                    city=candidate.city_name,
                    country_code=candidate.country_code,
                    plan_id=str(plan.id),
                ),
                score=item["score"],
                price_score=item["price_score"],
                convenience_score=item["convenience_score"],
                quality_score=item["quality_score"],
                location_score=item["location_score"],
                explanations=item["explanations"],
                score_breakdown=item["score_breakdown"],
                last_scored_at=timezone.now(),
            ),
        )

    return PackageOption.objects.bulk_create(records)

```

## planner/services/plan_service.py
```
import logging
from typing import Any

from django.db import transaction

from planner.models import PlanRequest
from planner.services.config import default_origin_iata
from planner.services.destination_service import resolve_origin_code

logger = logging.getLogger(__name__)


@transaction.atomic
def create_plan_request(user, payload: dict[str, Any]) -> PlanRequest:  # noqa: ANN001
    fallback_origin = default_origin_iata()
    raw_origin = payload.get("origin_input") or fallback_origin
    origin_input = str(raw_origin).strip().upper()
    if not origin_input:
        raise ValueError("Origin is required. Provide origin_input or DEFAULT_ORIGIN_IATA.")

    plan = PlanRequest.objects.create(
        user=user,
        origin_input=origin_input,
        origin_code=resolve_origin_code(origin_input),
        destination_country=payload["destination_country"].upper(),
        date_mode=payload["date_mode"],
        depart_date=payload.get("depart_date"),
        return_date=payload.get("return_date"),
        travel_month=payload.get("travel_month"),
        flexibility_days=payload.get("flexibility_days", 0),
        nights_min=payload["nights_min"],
        nights_max=payload["nights_max"],
        total_budget=payload["total_budget"],
        travelers=payload["travelers"],
        search_currency=payload.get("search_currency", "USD"),
        hotel_filters=payload.get("hotel_filters", {}),
        flight_filters=payload.get("flight_filters", {}),
        preference_weights=payload.get("preferences", {}),
        status=PlanRequest.Status.QUEUED,
        progress_message="Queued for links-only package estimation",
        progress_percent=5,
    )

    logger.info("Plan request created", extra={"plan_id": str(plan.id)})
    from planner.tasks import run_plan_pipeline

    run_plan_pipeline.delay(str(plan.id))
    return plan

```

## planner/services/provider_health.py
```
from __future__ import annotations

from datetime import timedelta
import math

from django.utils import timezone

from planner.models import ProviderCall, ProviderError
from planner.services.provider_registry import provider_status


def _percentile(values: list[int], pct: float) -> int | None:
    cleaned = sorted(v for v in values if isinstance(v, int) and v >= 0)
    if not cleaned:
        return None
    index = max(0, min(len(cleaned) - 1, math.ceil((pct / 100.0) * len(cleaned)) - 1))
    return cleaned[index]


def _provider_metrics(provider: str, enabled: bool) -> dict:
    one_hour_ago = timezone.now() - timedelta(hours=1)
    recent = ProviderCall.objects.filter(provider=provider, created_at__gte=one_hour_ago)
    total = recent.count()
    errors = recent.filter(success=False).count()
    latencies = [value for value in recent.values_list("latency_ms", flat=True) if value is not None]

    last_success = (
        ProviderCall.objects.filter(provider=provider, success=True)
        .order_by("-created_at")
        .values_list("created_at", flat=True)
        .first()
    )

    last_error = (
        ProviderError.objects.filter(provider=provider)
        .order_by("-created_at")
        .values("error_type", "context", "error_message", "created_at")
        .first()
    )

    summary = None
    if last_error:
        summary = {
            "error_type": last_error["error_type"],
            "context": last_error["context"],
            "message": (last_error["error_message"] or "")[:200],
            "created_at": last_error["created_at"],
        }

    return {
        "enabled": enabled,
        "last_success_at": last_success,
        "error_rate_1h": round((errors / total), 4) if total else 0.0,
        "latency_p95": _percentile(latencies, 95),
        "last_error_summary": summary,
        "calls_1h": total,
    }


def provider_health_payload() -> dict:
    flags = provider_status()
    payload = {
        "travelpayouts": _provider_metrics("travelpayouts", flags.get("travelpayouts_enabled", False)),
        "fx": _provider_metrics("fx", flags.get("fx_enabled", False)),
    }

    # Keep legacy keys for backward compatibility while links-only mode is default.
    payload["duffel"] = _provider_metrics("duffel", flags.get("duffel_enabled", False))
    payload["amadeus"] = _provider_metrics("amadeus", flags.get("amadeus_enabled", False))
    payload["expedia_rapid"] = _provider_metrics("expedia_rapid", flags.get("expedia_enabled", False))
    return payload

```

## planner/services/provider_registry.py
```
from __future__ import annotations

import os

from planner.services.config import links_only_enabled, travelpayouts_enabled
from planner.services.fx import fx_configured
from planner.services.providers.amadeus import AmadeusFlightsProvider
from planner.services.providers.duffel import DuffelFlightsProvider
from planner.services.providers.expedia_rapid import ExpediaRapidHotelsProvider
from planner.services.travelpayouts.adapter import TravelpayoutsAdapter


def get_market_provider() -> TravelpayoutsAdapter:
    return TravelpayoutsAdapter()


def get_flight_provider():
    if links_only_enabled():
        return None
    if os.getenv("DUFFEL_ACCESS_TOKEN"):
        return DuffelFlightsProvider()
    if os.getenv("AMADEUS_CLIENT_ID") and os.getenv("AMADEUS_CLIENT_SECRET"):
        return AmadeusFlightsProvider()
    return None


def get_hotel_provider():
    if links_only_enabled():
        return None
    if os.getenv("EXPEDIA_RAPID_KEY"):
        return ExpediaRapidHotelsProvider()
    return None


def provider_status() -> dict[str, bool]:
    legacy_duffel = bool(os.getenv("DUFFEL_ACCESS_TOKEN"))
    legacy_amadeus = bool(os.getenv("AMADEUS_CLIENT_ID") and os.getenv("AMADEUS_CLIENT_SECRET"))
    legacy_expedia = bool(os.getenv("EXPEDIA_RAPID_KEY"))

    return {
        "links_only_enabled": links_only_enabled(),
        "travelpayouts_enabled": travelpayouts_enabled(),
        "travelpayouts_token_configured": bool(os.getenv("TRAVELPAYOUTS_API_TOKEN")),
        "travelpayouts_marker_configured": bool(os.getenv("TRAVELPAYOUTS_MARKER") or os.getenv("TRIPPILOT_AFFILIATE_ID")),
        "duffel_enabled": legacy_duffel and not links_only_enabled(),
        "amadeus_enabled": legacy_amadeus and not links_only_enabled(),
        "expedia_enabled": legacy_expedia and not links_only_enabled(),
        "fx_enabled": fx_configured(),
    }

```

## planner/services/scoring.py
```
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass
class PackageScore:
    score: float
    price_score: float
    convenience_score: float
    quality_score: float
    location_score: float
    explanations: list[str]
    breakdown: dict


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _component_budget_fit(total_minor: int, budget_minor: int) -> tuple[float, str]:
    if budget_minor <= 0:
        return 50.0, "Budget not provided; using neutral budget-fit score"
    ratio = total_minor / budget_minor
    score = _clamp(100 - (abs(ratio - 0.82) * 110), 0, 100)
    if ratio <= 0.9:
        note = "Estimate is comfortably within budget"
    elif ratio <= 1.05:
        note = "Estimate is close to your budget target"
    else:
        note = "Estimate is above preferred budget"
    return round(score, 2), note


def _component_preference_match(preference_weights: dict[str, float], candidate_tags: list[str]) -> tuple[float, str]:
    if not preference_weights:
        return 60.0, "No explicit preferences provided; using balanced destination mix"
    tags = {tag.lower().strip() for tag in candidate_tags}
    total_weight = 0.0
    matched_weight = 0.0
    for key, raw_weight in preference_weights.items():
        label = str(key).lower().strip()
        try:
            weight = float(raw_weight)
        except (TypeError, ValueError):
            weight = 0.0
        if weight <= 0:
            continue
        total_weight += weight
        if label in tags:
            matched_weight += weight
    if total_weight == 0:
        return 58.0, "Preference weights were empty after normalization"
    score = _clamp((matched_weight / total_weight) * 100.0, 0, 100)
    note = "Strong match with selected travel preferences" if score >= 70 else "Partial preference match"
    return round(score, 2), note


def _component_seasonality(season_multiplier: float) -> tuple[float, str]:
    score = _clamp(95 - abs(season_multiplier - 1.0) * 130, 25, 100)
    if season_multiplier >= 1.12:
        note = "High-season timing may increase prices"
    elif season_multiplier <= 0.92:
        note = "Off-peak season likely lowers baseline prices"
    else:
        note = "Seasonality is in a moderate pricing window"
    return round(score, 2), note


def _component_travel_time(distance_band: str, nonstop_likelihood: float) -> tuple[float, str]:
    band_scores = {
        "short": 94.0,
        "medium": 78.0,
        "long": 60.0,
        "ultra_long": 43.0,
    }
    base_score = band_scores.get(distance_band, 70.0)
    blended = _clamp((base_score * 0.72) + (_clamp(nonstop_likelihood, 0.0, 1.0) * 100 * 0.28), 0, 100)
    if blended >= 80:
        note = "Shorter travel-time proxy with high nonstop likelihood"
    elif blended >= 60:
        note = "Moderate travel-time proxy"
    else:
        note = "Longer travel-time proxy for this route"
    return round(blended, 2), note


def _component_freshness(freshness_at: datetime | None, now: datetime | None = None) -> tuple[float, str]:
    if not freshness_at:
        return 45.0, "Freshness timestamp unavailable; conservative freshness score applied"
    current = now or freshness_at.tzinfo and datetime.now(tz=freshness_at.tzinfo) or datetime.utcnow()  # noqa: SIM108
    age_hours = max(0.0, (current - freshness_at).total_seconds() / 3600)
    if age_hours <= 6:
        return 100.0, "Price snapshot is very recent"
    if age_hours <= 24:
        return 85.0, "Price snapshot is within last 24h"
    if age_hours <= 72:
        return 66.0, "Price snapshot is a few days old"
    if age_hours <= 168:
        return 48.0, "Price snapshot is about a week old"
    return 34.0, "Price snapshot is stale and may drift"


def score_package(
    *,
    total_minor: int,
    budget_minor: int,
    preference_weights: dict[str, float],
    candidate_tags: list[str],
    season_multiplier: float,
    distance_band: str,
    nonstop_likelihood: float,
    freshness_at: datetime | None,
) -> PackageScore:
    budget_fit, budget_note = _component_budget_fit(total_minor, budget_minor)
    preference_match, preference_note = _component_preference_match(preference_weights, candidate_tags)
    seasonality, season_note = _component_seasonality(season_multiplier)
    travel_time_proxy, travel_note = _component_travel_time(distance_band, nonstop_likelihood)
    freshness, freshness_note = _component_freshness(freshness_at)

    score = (
        (budget_fit * 0.35)
        + (preference_match * 0.20)
        + (seasonality * 0.15)
        + (travel_time_proxy * 0.20)
        + (freshness * 0.10)
    )

    explanations = [budget_note, preference_note, season_note, travel_note, freshness_note]
    breakdown = {
        "budget_fit": round(budget_fit, 2),
        "preference_match": round(preference_match, 2),
        "seasonality": round(seasonality, 2),
        "travel_time_proxy": round(travel_time_proxy, 2),
        "freshness": round(freshness, 2),
        "weights": {
            "budget_fit": 0.35,
            "preference_match": 0.20,
            "seasonality": 0.15,
            "travel_time_proxy": 0.20,
            "freshness": 0.10,
        },
        "explanations": explanations,
    }

    return PackageScore(
        score=round(score, 2),
        price_score=round(budget_fit, 2),
        convenience_score=round(travel_time_proxy, 2),
        quality_score=round(preference_match, 2),
        location_score=round(seasonality, 2),
        explanations=explanations,
        breakdown=breakdown,
    )

```

## planner/services/travelpayouts/__init__.py
```
from planner.services.travelpayouts.adapter import TravelpayoutsAdapter
from planner.services.travelpayouts.types import CandidateEstimate

__all__ = ["CandidateEstimate", "TravelpayoutsAdapter"]

```

## planner/services/travelpayouts/adapter.py
```
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from django.utils import timezone

from planner.services.config import travelpayouts_base_currency, travelpayouts_enabled
from planner.services.providers.base import ProviderException
from planner.services.travelpayouts.client import TravelpayoutsClient
from planner.services.travelpayouts.fallbacks import estimate_fallback_prices
from planner.services.travelpayouts.types import CandidateEstimate

_PRICE_KEYS = {
    "price",
    "min_price",
    "max_price",
    "average_price",
    "avg_price",
    "value",
    "amount",
    "total_price",
}
_TIMESTAMP_HINTS = {"updated", "update", "fetched", "timestamp", "expires", "as_of"}


def _to_decimal(value: Any) -> Decimal | None:
    try:
        decimal_value = Decimal(str(value))
    except Exception:  # noqa: BLE001
        return None
    if decimal_value <= 0:
        return None
    if decimal_value > Decimal("50000"):
        return None
    return decimal_value


def _extract_price_values(node: Any) -> list[Decimal]:
    prices: list[Decimal] = []
    if isinstance(node, dict):
        for key, value in node.items():
            normalized_key = str(key).lower()
            if normalized_key in _PRICE_KEYS:
                decimal_value = _to_decimal(value)
                if decimal_value is not None:
                    prices.append(decimal_value)
            elif isinstance(value, (dict, list)):
                prices.extend(_extract_price_values(value))
    elif isinstance(node, list):
        for item in node:
            prices.extend(_extract_price_values(item))
    return prices


def _extract_timestamp_values(node: Any) -> list[datetime]:
    timestamps: list[datetime] = []
    if isinstance(node, dict):
        for key, value in node.items():
            key_text = str(key).lower()
            if any(hint in key_text for hint in _TIMESTAMP_HINTS):
                parsed = _parse_datetime(value)
                if parsed:
                    timestamps.append(parsed)
            if isinstance(value, (dict, list)):
                timestamps.extend(_extract_timestamp_values(value))
    elif isinstance(node, list):
        for item in node:
            timestamps.extend(_extract_timestamp_values(item))
    return timestamps


def _parse_datetime(value: Any) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    raw = value.strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if timezone.is_naive(parsed):
        return timezone.make_aware(parsed, timezone.utc)
    return parsed


def _extract_destination_prices(payload: dict[str, Any], destination_code: str, destination_city: str) -> list[Decimal]:
    data = payload.get("data")
    probes = [destination_code, destination_code.upper(), destination_city, destination_city.upper(), destination_city.lower()]
    collected: list[Decimal] = []

    if isinstance(data, dict):
        for probe in probes:
            if probe in data:
                collected.extend(_extract_price_values(data[probe]))

    if not collected:
        collected.extend(_extract_price_values(payload))

    return [price for price in collected if price >= Decimal("20")]


def _quantize_money(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"))


def _clamp(value: Decimal, low: Decimal, high: Decimal) -> Decimal:
    return max(low, min(high, value))


class TravelpayoutsAdapter:
    provider = "travelpayouts"

    def __init__(self, client: TravelpayoutsClient | None = None) -> None:
        self.client = client or TravelpayoutsClient()

    def estimate(
        self,
        *,
        origin_code: str,
        destination_code: str,
        destination_city: str,
        destination_country: str,
        depart_date: date,
        return_date: date | None,
        travelers: int,
        tier: str,
        tags: list[str],
        origin_coords: tuple[float, float] | None,
        destination_coords: tuple[float, float] | None,
        nonstop_likelihood: float | None,
        preferred_currency: str | None = None,
    ) -> CandidateEstimate:
        currency = (preferred_currency or travelpayouts_base_currency()).upper()

        fallback = estimate_fallback_prices(
            origin_coords=origin_coords,
            destination_coords=destination_coords,
            depart_date=depart_date,
            travelers=travelers,
            tier=tier,
            nonstop_likelihood=nonstop_likelihood,
        )

        endpoint_status: dict[str, str] = {}
        endpoint_payloads: dict[str, Any] = {}
        endpoint_latencies: list[int] = []
        live_prices: list[Decimal] = []
        freshness_hints: list[datetime] = []
        primary_error: ProviderException | None = None

        enabled = travelpayouts_enabled() and self.client.enabled
        if enabled:
            endpoint_calls = {
                "cheap": lambda: self.client.get_cheap_prices(
                    origin=origin_code,
                    destination=destination_code,
                    depart_date=depart_date,
                    return_date=return_date,
                    currency=currency,
                ),
                "calendar": lambda: self.client.get_calendar_prices(
                    origin=origin_code,
                    destination=destination_code,
                    depart_date=depart_date,
                    return_date=return_date,
                    currency=currency,
                ),
                "city_directions": lambda: self.client.get_city_directions(
                    origin=origin_code,
                    currency=currency,
                ),
            }
            for endpoint, handler in endpoint_calls.items():
                try:
                    payload, latency_ms = handler()
                    endpoint_status[endpoint] = "ok"
                    endpoint_payloads[endpoint] = payload
                    endpoint_latencies.append(latency_ms)
                    live_prices.extend(_extract_destination_prices(payload, destination_code, destination_city))
                    freshness_hints.extend(_extract_timestamp_values(payload))
                except ProviderException as exc:
                    endpoint_status[endpoint] = exc.error_type
                    endpoint_payloads[endpoint] = {
                        "error_type": exc.error_type,
                        "http_status": exc.http_status,
                    }
                    if primary_error is None:
                        primary_error = exc
        else:
            endpoint_status["travelpayouts"] = "disabled"
            endpoint_payloads["travelpayouts"] = {"reason": "TRAVELPAYOUTS_ENABLED=false or token missing"}

        source = "fallback"
        flight_min = fallback.flight_min
        flight_max = fallback.flight_max

        if live_prices:
            raw_min = min(live_prices)
            raw_max = max(live_prices)
            if raw_max <= raw_min:
                raw_max = raw_min * Decimal("1.22")

            bounded_min = _clamp(raw_min, fallback.flight_min * Decimal("0.55"), fallback.flight_max * Decimal("1.75"))
            bounded_max = _clamp(raw_max, bounded_min * Decimal("1.06"), fallback.flight_max * Decimal("2.20"))
            flight_min = _quantize_money(bounded_min)
            flight_max = _quantize_money(bounded_max)
            source = "travelpayouts"

        ratio = Decimal("1")
        fallback_mid = (fallback.flight_min + fallback.flight_max) / Decimal("2")
        if fallback_mid > 0:
            ratio = ((flight_min + flight_max) / Decimal("2")) / fallback_mid
        ratio = _clamp(ratio, Decimal("0.82"), Decimal("1.36"))

        hotel_nightly_min = _quantize_money(fallback.hotel_nightly_min * ratio)
        hotel_nightly_max = _quantize_money(fallback.hotel_nightly_max * ratio)

        freshness_at = max(freshness_hints) if freshness_hints else timezone.now()
        latency_ms = max(endpoint_latencies) if endpoint_latencies else (primary_error.latency_ms if primary_error else None)

        error_type: str | None = None
        http_status: int | None = None
        error_summary = ""
        if source == "fallback" and primary_error:
            error_type = primary_error.error_type
            http_status = primary_error.http_status
            error_summary = str(primary_error)

        raw_payload = {
            "origin_code": origin_code,
            "destination_code": destination_code,
            "destination_city": destination_city,
            "destination_country": destination_country,
            "tier": tier,
            "tags": tags,
            "fallback": {
                "flight_min": str(fallback.flight_min),
                "flight_max": str(fallback.flight_max),
                "hotel_nightly_min": str(fallback.hotel_nightly_min),
                "hotel_nightly_max": str(fallback.hotel_nightly_max),
                "distance_km": round(fallback.distance_km, 2),
                "distance_band": fallback.distance_band,
                "season_multiplier": fallback.season_multiplier,
                "nonstop_likelihood": fallback.nonstop_likelihood,
            },
            "live_price_points": [str(price) for price in live_prices[:40]],
            "endpoints": endpoint_payloads,
        }

        return CandidateEstimate(
            provider=self.provider,
            source=source,
            currency=currency,
            flight_min=flight_min,
            flight_max=flight_max,
            hotel_nightly_min=hotel_nightly_min,
            hotel_nightly_max=hotel_nightly_max,
            freshness_at=freshness_at,
            distance_km=fallback.distance_km,
            distance_band=fallback.distance_band,
            travel_time_minutes=fallback.travel_time_minutes,
            nonstop_likelihood=fallback.nonstop_likelihood,
            season_multiplier=fallback.season_multiplier,
            tier=tier,
            tags=tags,
            raw_payload=raw_payload,
            endpoints=endpoint_status,
            error_type=error_type,
            http_status=http_status,
            error_summary=error_summary[:500],
            latency_ms=latency_ms,
        )

```

## planner/services/travelpayouts/client.py
```
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

```

## planner/services/travelpayouts/fallbacks.py
```
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from functools import lru_cache
from pathlib import Path

from planner.services.geo import haversine_km

BASELINE_PATH = Path(__file__).resolve().parents[2] / "data" / "pricing_baselines.json"


@dataclass
class FallbackPriceEstimate:
    flight_min: Decimal
    flight_max: Decimal
    hotel_nightly_min: Decimal
    hotel_nightly_max: Decimal
    distance_km: float
    distance_band: str
    travel_time_minutes: int
    nonstop_likelihood: float
    season_multiplier: float


@lru_cache(maxsize=1)
def load_pricing_baselines() -> dict:
    with BASELINE_PATH.open("r", encoding="utf-8-sig") as handle:
        return json.load(handle)


def season_multiplier_for_month(month: int) -> float:
    data = load_pricing_baselines().get("season_multipliers", {})
    return float(data.get(str(int(month)), 1.0))


def country_default_profile(country_code: str) -> dict:
    defaults = load_pricing_baselines().get("country_defaults", {})
    return defaults.get(country_code.upper(), {"tier": "standard", "tags": ["culture", "food"], "nonstop_likelihood": 0.55})


def airport_override_profile(airport_code: str) -> dict:
    overrides = load_pricing_baselines().get("airport_overrides", {})
    return overrides.get(airport_code.upper(), {})


def tier_profile(tier_name: str) -> dict:
    tiers = load_pricing_baselines().get("hotel_tiers", {})
    return tiers.get(tier_name, tiers.get("standard", {"nightly_min": 80, "nightly_max": 190, "star_rating": 3.6, "guest_rating": 8.0}))


def distance_profile(distance_km: float) -> dict:
    bands = load_pricing_baselines().get("distance_bands_km", [])
    for band in bands:
        if distance_km <= float(band.get("max_km", 0)):
            return band
    return bands[-1] if bands else {
        "band": "medium",
        "flight_min": 260,
        "flight_max": 680,
        "travel_time_hours": 5.0,
        "nonstop_likelihood": 0.72,
    }


def estimate_fallback_prices(
    *,
    origin_coords: tuple[float, float] | None,
    destination_coords: tuple[float, float] | None,
    depart_date: date,
    travelers: int,
    tier: str,
    nonstop_likelihood: float | None = None,
) -> FallbackPriceEstimate:
    if origin_coords and destination_coords:
        distance_km = float(
            haversine_km(
                origin_coords[0],
                origin_coords[1],
                destination_coords[0],
                destination_coords[1],
            ),
        )
    else:
        distance_km = 2400.0

    band = distance_profile(distance_km)
    season_multiplier = season_multiplier_for_month(depart_date.month)
    traveler_count = max(1, int(travelers))

    base_flight_min = Decimal(str(band.get("flight_min", 260)))
    base_flight_max = Decimal(str(band.get("flight_max", 680)))

    traveler_spread = Decimal("1") + (Decimal(str(max(0, traveler_count - 1))) * Decimal("0.09"))
    flight_min = (base_flight_min * Decimal(str(season_multiplier)) * traveler_count).quantize(Decimal("0.01"))
    flight_max = (base_flight_max * Decimal(str(season_multiplier)) * traveler_count * traveler_spread).quantize(Decimal("0.01"))

    hotel = tier_profile(tier)
    hotel_min_base = Decimal(str(hotel.get("nightly_min", 80)))
    hotel_max_base = Decimal(str(hotel.get("nightly_max", 190)))
    occupancy_factor = Decimal("1") + (Decimal(str(max(0, traveler_count - 2))) * Decimal("0.18"))

    hotel_nightly_min = (hotel_min_base * Decimal(str(season_multiplier)) * occupancy_factor).quantize(Decimal("0.01"))
    hotel_nightly_max = (hotel_max_base * Decimal(str(season_multiplier)) * occupancy_factor).quantize(Decimal("0.01"))

    profile_nonstop = float(band.get("nonstop_likelihood", 0.6))
    if nonstop_likelihood is not None:
        profile_nonstop = float(nonstop_likelihood)

    travel_minutes = int(float(band.get("travel_time_hours", 5.0)) * 60)

    return FallbackPriceEstimate(
        flight_min=flight_min,
        flight_max=flight_max,
        hotel_nightly_min=hotel_nightly_min,
        hotel_nightly_max=hotel_nightly_max,
        distance_km=distance_km,
        distance_band=str(band.get("band", "medium")),
        travel_time_minutes=max(90, travel_minutes),
        nonstop_likelihood=max(0.0, min(1.0, profile_nonstop)),
        season_multiplier=season_multiplier,
    )

```

## planner/services/travelpayouts/types.py
```
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any


@dataclass
class CandidateEstimate:
    provider: str
    source: str
    currency: str
    flight_min: Decimal
    flight_max: Decimal
    hotel_nightly_min: Decimal
    hotel_nightly_max: Decimal
    freshness_at: datetime
    distance_km: float
    distance_band: str
    travel_time_minutes: int
    nonstop_likelihood: float
    season_multiplier: float
    tier: str
    tags: list[str]
    raw_payload: dict[str, Any] = field(default_factory=dict)
    endpoints: dict[str, str] = field(default_factory=dict)
    error_type: str | None = None
    http_status: int | None = None
    error_summary: str = ""
    latency_ms: int | None = None

    @property
    def flight_mid(self) -> Decimal:
        return ((self.flight_min + self.flight_max) / Decimal("2")).quantize(Decimal("0.01"))

    @property
    def hotel_nightly_mid(self) -> Decimal:
        return ((self.hotel_nightly_min + self.hotel_nightly_max) / Decimal("2")).quantize(Decimal("0.01"))

```

## planner/tasks.py
```
from __future__ import annotations

import logging
import os
from datetime import datetime, time
from decimal import Decimal

from celery import chord, shared_task
from django.utils import timezone

from planner.models import (
    DestinationCandidate,
    FlightOption,
    HotelOption,
    PackageOption,
    PlanRequest,
    Profile,
    ProviderCall,
    ProviderError,
)
from planner.services.deeplinks import build_flight_search_link, build_hotel_search_link
from planner.services.destination_service import airport_coordinates, build_destination_candidates
from planner.services.fx import refresh_fx_rates, to_minor_units
from planner.services.package_builder import build_packages_for_plan
from planner.services.provider_registry import get_market_provider
from planner.services.providers.base import ProviderException
from planner.services.travelpayouts.fallbacks import tier_profile
from planner.services.travelpayouts.types import CandidateEstimate
from trip_pilot.logging import clear_request_context, set_request_context

logger = logging.getLogger(__name__)


def _set_status(plan: PlanRequest, status: str, message: str, percent: int, *, error_message: str = "") -> None:
    updates = {
        "status": status,
        "progress_message": message,
        "progress_percent": percent,
        "error_message": error_message,
    }
    if status == PlanRequest.Status.SEARCHING_FLIGHTS and not plan.started_at:
        updates["started_at"] = timezone.now()
    if status in {PlanRequest.Status.COMPLETED, PlanRequest.Status.FAILED}:
        updates["completed_at"] = timezone.now()
    PlanRequest.objects.filter(pk=plan.pk).update(**updates)


def _correlation_id(plan_id: str, provider: str, target: str) -> str:
    return f"{plan_id}:{provider}:{target}"[:64]


def _record_provider_call(
    *,
    provider: str,
    plan: PlanRequest | None,
    success: bool,
    error_type: str = ProviderError.ErrorType.UNKNOWN,
    http_status: int | None = None,
    latency_ms: int | None = None,
    correlation_id: str = "",
) -> None:
    ProviderCall.objects.create(
        provider=provider,
        plan=plan,
        success=success,
        error_type=error_type,
        http_status=http_status,
        latency_ms=latency_ms,
        correlation_id=correlation_id[:64],
    )


def _record_provider_error(
    *,
    plan: PlanRequest,
    provider: str,
    context: str,
    message: str,
    error_type: str = ProviderError.ErrorType.UNKNOWN,
    http_status: int | None = None,
    latency_ms: int | None = None,
    raw_payload: dict | None = None,
) -> None:
    ProviderError.objects.create(
        plan=plan,
        provider=provider,
        context=context,
        error_message=message,
        error_type=error_type,
        http_status=http_status,
        provider_latency_ms=latency_ms,
        raw_payload=raw_payload or {},
    )


def _refresh_plan_fx(plan: PlanRequest) -> None:
    target = (plan.search_currency or "USD").upper()
    currencies = set(plan.flight_options.values_list("currency", flat=True))
    currencies.update(plan.hotel_options.values_list("currency", flat=True))
    currencies.add(target)
    for base_currency in sorted(code.upper() for code in currencies if code):
        refresh_fx_rates(base_currency=base_currency, quote_currencies={target})


def _safe_datetime(day) -> datetime | None:  # noqa: ANN001
    if not day:
        return None
    return timezone.make_aware(datetime.combine(day, time(hour=9, minute=0)))


def _persist_candidate_options(
    *,
    plan: PlanRequest,
    candidate: DestinationCandidate,
    estimate: CandidateEstimate,
    depart_date,
    return_date,
) -> None:  # noqa: ANN001
    FlightOption.objects.filter(plan=plan, candidate=candidate).delete()
    HotelOption.objects.filter(plan=plan, candidate=candidate).delete()

    avg_nights = max(1, int((plan.nights_min + plan.nights_max) / 2))
    destination_label = f"{candidate.city_name}-{candidate.country_code}"
    cabin = str((plan.flight_filters or {}).get("cabin", "economy")).lower()
    tier = tier_profile(estimate.tier)

    flight_deeplink = build_flight_search_link(
        origin=plan.origin_code,
        destination=candidate.airport_code,
        depart_date=depart_date,
        return_date=return_date,
        travelers=plan.travelers,
        cabin=cabin,
        plan_id=str(plan.id),
        destination_label=destination_label,
    )

    hotel_deeplink = build_hotel_search_link(
        city=candidate.city_name,
        country_code=candidate.country_code,
        checkin=depart_date,
        checkout=return_date or depart_date,
        adults=plan.travelers,
        plan_id=str(plan.id),
    )

    stops_by_band = {"short": 0, "medium": 1, "long": 1, "ultra_long": 2}
    flight_payload = {
        "estimated_min": str(estimate.flight_min),
        "estimated_max": str(estimate.flight_max),
        "distance_km": round(estimate.distance_km, 2),
        "distance_band": estimate.distance_band,
        "nonstop_likelihood": estimate.nonstop_likelihood,
        "season_multiplier": estimate.season_multiplier,
        "data_source": estimate.source,
        "provider_endpoints": estimate.endpoints,
    }
    flight_payload.update(estimate.raw_payload)

    hotel_payload = {
        "nightly_min": str(estimate.hotel_nightly_min),
        "nightly_max": str(estimate.hotel_nightly_max),
        "distance_km": round(estimate.distance_km, 2),
        "distance_band": estimate.distance_band,
        "nonstop_likelihood": estimate.nonstop_likelihood,
        "season_multiplier": estimate.season_multiplier,
        "data_source": estimate.source,
        "provider_endpoints": estimate.endpoints,
    }
    hotel_payload.update(estimate.raw_payload)

    flight = FlightOption.objects.create(
        plan=plan,
        candidate=candidate,
        provider=estimate.provider,
        external_offer_id=f"{candidate.airport_code}-{depart_date:%Y%m%d}-{estimate.source}",
        origin_airport=plan.origin_code,
        destination_airport=candidate.airport_code,
        departure_at=_safe_datetime(depart_date),
        return_at=_safe_datetime(return_date),
        airline_codes=[],
        stops=stops_by_band.get(estimate.distance_band, 1),
        duration_minutes=max(90, int(estimate.travel_time_minutes)),
        cabin_class=cabin,
        currency=estimate.currency,
        total_price=estimate.flight_mid,
        amount_minor=to_minor_units(estimate.flight_mid),
        deeplink_url=flight_deeplink,
        raw_payload=flight_payload,
        last_checked_at=estimate.freshness_at,
    )

    hotel_total = (estimate.hotel_nightly_mid * Decimal(str(avg_nights))).quantize(Decimal("0.01"))

    HotelOption.objects.create(
        plan=plan,
        candidate=candidate,
        provider=estimate.provider,
        external_offer_id=f"{candidate.airport_code}-hotel-{depart_date:%Y%m%d}-{estimate.source}",
        name=f"{candidate.city_name} partner hotels",
        star_rating=float(tier.get("star_rating", 3.7)),
        guest_rating=float(tier.get("guest_rating", 8.0)),
        neighborhood="City center",
        latitude=candidate.latitude,
        longitude=candidate.longitude,
        amenities=(candidate.metadata or {}).get("tags", []),
        currency=estimate.currency,
        total_price=hotel_total,
        amount_minor=to_minor_units(hotel_total),
        deeplink_url=hotel_deeplink,
        raw_payload=hotel_payload,
        distance_km=estimate.distance_km,
        last_checked_at=estimate.freshness_at,
    )

    logger.info(
        "Candidate estimates persisted",
        extra={
            "plan_id": str(plan.id),
            "candidate": candidate.airport_code,
            "source": estimate.source,
            "flight_option": str(flight.id),
        },
    )


@shared_task(bind=True, soft_time_limit=90, time_limit=120)
def run_plan_pipeline(self, plan_id: str) -> None:  # noqa: ARG001
    set_request_context(plan_id=plan_id)
    try:
        plan = PlanRequest.objects.get(pk=plan_id)
        _set_status(plan, PlanRequest.Status.SEARCHING_FLIGHTS, "Building destination candidates...", 12)
        candidates = build_destination_candidates(plan, max_items=8)
        if not candidates:
            _set_status(
                plan,
                PlanRequest.Status.FAILED,
                "No destination data available for selected country.",
                100,
                error_message="Missing country data.",
            )
            return

        _set_status(plan, PlanRequest.Status.SEARCHING_FLIGHTS, "Fetching market snapshots from Travelpayouts...", 26)
        jobs = [fetch_candidate_market_data.s(plan_id, candidate.id) for candidate in candidates]
        chord(jobs)(market_data_stage_complete.s(plan_id))
    finally:
        clear_request_context()


@shared_task(bind=True, soft_time_limit=90, time_limit=120)
def fetch_candidate_market_data(self, plan_id: str, candidate_id: int) -> dict:  # noqa: ARG001
    set_request_context(plan_id=plan_id)
    try:
        plan = PlanRequest.objects.get(pk=plan_id)
        candidate = plan.destination_candidates.get(pk=candidate_id)
        provider = get_market_provider()
        correlation_id = _correlation_id(plan_id, "travelpayouts", candidate.airport_code)

        depart_date, return_date = plan.resolve_dates()
        origin_coords = airport_coordinates(plan.origin_code)
        destination_coords = None
        if candidate.latitude is not None and candidate.longitude is not None:
            destination_coords = (float(candidate.latitude), float(candidate.longitude))
        else:
            destination_coords = airport_coordinates(candidate.airport_code)

        metadata = candidate.metadata or {}
        estimate = provider.estimate(
            origin_code=plan.origin_code,
            destination_code=candidate.airport_code,
            destination_city=candidate.city_name,
            destination_country=candidate.country_code,
            depart_date=depart_date,
            return_date=return_date,
            travelers=plan.travelers,
            tier=str(metadata.get("tier") or "standard"),
            tags=[str(tag).lower() for tag in metadata.get("tags", [])],
            origin_coords=origin_coords,
            destination_coords=destination_coords,
            nonstop_likelihood=metadata.get("nonstop_likelihood"),
            preferred_currency=plan.search_currency,
        )

        _persist_candidate_options(
            plan=plan,
            candidate=candidate,
            estimate=estimate,
            depart_date=depart_date,
            return_date=return_date,
        )

        success = estimate.source == "travelpayouts"
        error_type = estimate.error_type or ProviderError.ErrorType.EMPTY
        if success:
            error_type = ProviderError.ErrorType.UNKNOWN

        _record_provider_call(
            provider="travelpayouts",
            plan=plan,
            success=success,
            error_type=error_type,
            http_status=estimate.http_status,
            latency_ms=estimate.latency_ms,
            correlation_id=correlation_id,
        )

        if estimate.error_summary:
            _record_provider_error(
                plan=plan,
                provider="travelpayouts",
                context=f"candidate={candidate.airport_code}",
                message=estimate.error_summary,
                error_type=estimate.error_type or ProviderError.ErrorType.UNKNOWN,
                http_status=estimate.http_status,
                latency_ms=estimate.latency_ms,
                raw_payload={"endpoints": estimate.endpoints},
            )

        return {
            "candidate_id": candidate_id,
            "source": estimate.source,
            "freshness": estimate.freshness_at.isoformat(),
        }
    except (PlanRequest.DoesNotExist, DestinationCandidate.DoesNotExist):
        return {"candidate_id": candidate_id, "source": "missing"}
    except ProviderException as exc:
        plan = PlanRequest.objects.filter(pk=plan_id).first()
        if plan:
            _record_provider_error(
                plan=plan,
                provider="travelpayouts",
                context=f"candidate={candidate_id}",
                message=str(exc),
                error_type=exc.error_type,
                http_status=exc.http_status,
                latency_ms=exc.latency_ms,
                raw_payload=exc.raw_payload,
            )
            _record_provider_call(
                provider="travelpayouts",
                plan=plan,
                success=False,
                error_type=exc.error_type,
                http_status=exc.http_status,
                latency_ms=exc.latency_ms,
                correlation_id=_correlation_id(plan_id, "travelpayouts", str(candidate_id)),
            )
        if exc.error_type in {ProviderError.ErrorType.TIMEOUT, ProviderError.ErrorType.RATE_LIMIT} and self.request.retries < 2:
            raise self.retry(exc=exc, countdown=2 ** self.request.retries)
        return {"candidate_id": candidate_id, "source": "error"}
    except Exception as exc:  # noqa: BLE001
        plan = PlanRequest.objects.filter(pk=plan_id).first()
        if plan:
            _record_provider_error(
                plan=plan,
                provider="travelpayouts",
                context=f"candidate={candidate_id}",
                message=f"Unexpected market data error: {exc}",
                error_type=ProviderError.ErrorType.UNKNOWN,
            )
            _record_provider_call(
                provider="travelpayouts",
                plan=plan,
                success=False,
                error_type=ProviderError.ErrorType.UNKNOWN,
                correlation_id=_correlation_id(plan_id, "travelpayouts", str(candidate_id)),
            )
        return {"candidate_id": candidate_id, "source": "error"}
    finally:
        clear_request_context()


@shared_task(bind=True, soft_time_limit=90, time_limit=120)
def market_data_stage_complete(self, results: list[dict], plan_id: str) -> None:  # noqa: ARG001
    set_request_context(plan_id=plan_id)
    try:
        plan = PlanRequest.objects.get(pk=plan_id)
        _set_status(plan, PlanRequest.Status.SEARCHING_HOTELS, "Normalizing currency estimates...", 62)
        build_packages_task.delay(plan_id)
    finally:
        clear_request_context()


@shared_task(bind=True, soft_time_limit=120, time_limit=180)
def build_packages_task(self, plan_id: str) -> None:  # noqa: ARG001
    set_request_context(plan_id=plan_id)
    try:
        plan = PlanRequest.objects.get(pk=plan_id)
        _set_status(plan, PlanRequest.Status.BUILDING_PACKAGES, "Scoring links-only packages...", 84)
        _refresh_plan_fx(plan)
        build_packages_for_plan(plan, sort_mode="best_value", max_packages=10)
        package_count = plan.package_options.count()
        if package_count == 0:
            _set_status(
                plan,
                PlanRequest.Status.FAILED,
                "No package estimates returned for this search.",
                100,
                error_message="No package combinations available.",
            )
            return
        _set_status(plan, PlanRequest.Status.COMPLETED, f"Found {package_count} ranked links-only packages.", 100)
    except Exception as exc:  # noqa: BLE001
        plan = PlanRequest.objects.filter(pk=plan_id).first()
        if plan:
            _set_status(plan, PlanRequest.Status.FAILED, "Package build failed.", 100, error_message=str(exc))
        raise
    finally:
        clear_request_context()


@shared_task(bind=True, soft_time_limit=120, time_limit=180)
def refresh_top_packages_task(self, plan_id: str, limit: int = 5) -> int:  # noqa: ARG001
    set_request_context(plan_id=plan_id)
    try:
        plan = PlanRequest.objects.get(pk=plan_id)
        _set_status(plan, PlanRequest.Status.SEARCHING_FLIGHTS, "Refreshing top links-only estimates...", 35)

        top_packages = list(plan.package_options.select_related("candidate").order_by("-score", "rank")[:limit])
        candidate_ids = sorted({package.candidate_id for package in top_packages})
        if not candidate_ids:
            candidate_ids = list(plan.destination_candidates.values_list("id", flat=True)[:limit])

        if not candidate_ids:
            _set_status(plan, PlanRequest.Status.FAILED, "No candidates available to refresh.", 100, error_message="No candidates")
            return 0

        for candidate_id in candidate_ids:
            fetch_candidate_market_data(plan_id, candidate_id)

        _set_status(plan, PlanRequest.Status.SEARCHING_HOTELS, "Normalizing refreshed estimates...", 60)
        _refresh_plan_fx(plan)

        _set_status(plan, PlanRequest.Status.BUILDING_PACKAGES, "Re-scoring packages...", 85)
        build_packages_for_plan(plan, sort_mode="best_value", max_packages=10)
        PackageOption.objects.filter(plan=plan).update(last_scored_at=timezone.now())
        _set_status(plan, PlanRequest.Status.COMPLETED, "Top package refresh completed.", 100)
        return len(candidate_ids)
    finally:
        clear_request_context()


# Backward-compatible task names retained for monitoring/ops scripts.
@shared_task(bind=True, soft_time_limit=90, time_limit=120)
def fetch_flights_for_candidate(self, plan_id: str, candidate_id: int) -> dict:  # noqa: ARG001
    return fetch_candidate_market_data(plan_id, candidate_id)


@shared_task(bind=True, soft_time_limit=90, time_limit=120)
def fetch_hotels_for_candidate(self, plan_id: str, candidate_id: int) -> dict:  # noqa: ARG001
    return fetch_candidate_market_data(plan_id, candidate_id)


@shared_task(bind=True, soft_time_limit=90, time_limit=120)
def flights_stage_complete(self, results: list[dict], plan_id: str) -> None:  # noqa: ARG001
    return market_data_stage_complete(results, plan_id)


@shared_task(bind=True, soft_time_limit=90, time_limit=120)
def hotels_stage_complete(self, results: list[dict], plan_id: str) -> None:  # noqa: ARG001
    return market_data_stage_complete(results, plan_id)


@shared_task(bind=True, autoretry_for=(Exception,), retry_backoff=True, retry_jitter=True, retry_kwargs={"max_retries": 3})
def refresh_fx_rates_daily(self) -> int:  # noqa: ARG001
    quote_currencies = set(
        code.upper()
        for code in Profile.objects.exclude(preferred_currency="").values_list("preferred_currency", flat=True)
    )
    quote_currencies.update(
        code.upper()
        for code in PlanRequest.objects.exclude(search_currency="").values_list("search_currency", flat=True)
    )
    from_env = [code.strip().upper() for code in os.getenv("FX_QUOTE_CURRENCIES", "USD,EUR,GBP,CAD").split(",") if code.strip()]
    quote_currencies.update(from_env)

    count = refresh_fx_rates(base_currency="USD", quote_currencies=quote_currencies)
    _record_provider_call(provider="fx", plan=None, success=True, error_type=ProviderError.ErrorType.UNKNOWN)
    return count


@shared_task
def cleanup_old_plans(days: int = 21) -> int:
    cutoff = timezone.now() - timezone.timedelta(days=days)
    stale = PlanRequest.objects.filter(
        status__in=[PlanRequest.Status.COMPLETED, PlanRequest.Status.FAILED],
        created_at__lt=cutoff,
    )
    count = stale.count()
    stale.delete()
    return count

```

## planner/templates/base.html
```
{% load static %}
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{% block title %}TriPPlanner{% endblock %}</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@400;500;600;700;800&family=Manrope:wght@400;500;700&display=swap" rel="stylesheet">
  <script src="https://cdn.tailwindcss.com"></script>
  <script>
    tailwind.config = {
      theme: {
        extend: {
          colors: {
            ink: '#12263A',
            ocean: '#1B4965',
            mint: '#5FA8A6',
            sky: '#CAE9FF',
            sand: '#FFF7E8'
          }
        }
      }
    }
  </script>
  <script src="https://unpkg.com/htmx.org@1.9.12"></script>
  <script defer src="https://unpkg.com/alpinejs@3.14.8/dist/cdn.min.js"></script>
  <link rel="stylesheet" href="{% static 'trippilot/app.css' %}">
</head>
<body class="bg-sand text-ink min-h-screen">
  <div class="min-h-screen bg-[radial-gradient(circle_at_15%_25%,#ffffff_0%,#f7fbff_40%,#f5f1e8_100%)]">
    <header class="border-b border-sky/40 bg-white/80 backdrop-blur">
      <nav class="mx-auto flex max-w-7xl items-center justify-between px-6 py-4">
        <a href="{% url 'planner:landing' %}" class="text-2xl font-extrabold tracking-tight">TriPPlanner</a>
        <div class="flex items-center gap-3 text-sm font-medium">
          <a href="{% url 'planner:wizard' %}" class="rounded-xl border border-ink/10 bg-white px-4 py-2 hover:bg-sky/30">Planner</a>
          {% if request.user.is_authenticated %}
            <a href="{% url 'profile' %}" class="rounded-xl border border-ink/10 bg-white px-4 py-2 hover:bg-sky/30">Profile</a>
            <form action="{% url 'logout' %}" method="post" class="inline">
              {% csrf_token %}
              <button class="rounded-xl bg-ink px-4 py-2 text-white hover:bg-ocean">Logout</button>
            </form>
          {% else %}
            <a href="{% url 'login' %}" class="rounded-xl border border-ink/10 bg-white px-4 py-2 hover:bg-sky/30">Login</a>
            <a href="{% url 'signup' %}" class="rounded-xl bg-ink px-4 py-2 text-white hover:bg-ocean">Create Account</a>
          {% endif %}
        </div>
      </nav>
    </header>

    <main class="mx-auto max-w-7xl px-6 py-8">
      {% if messages %}
        <div class="mb-6 space-y-2">
          {% for message in messages %}
            <div class="rounded-xl border border-mint/30 bg-white px-4 py-3 text-sm shadow-sm">{{ message }}</div>
          {% endfor %}
        </div>
      {% endif %}
      {% block content %}{% endblock %}
    </main>

    <footer class="mt-12 border-t border-sky/40 bg-white/70">
      <div class="mx-auto max-w-7xl px-6 py-6 text-xs text-ink/70">
        TriPPlanner is links-only: ranked package estimates with outbound affiliate links. No checkout or booking on TriPPlanner.
      </div>
    </footer>
  </div>

  <script>
    document.body.addEventListener('htmx:configRequest', function (event) {
      const tokenElement = document.querySelector('[name=csrfmiddlewaretoken]');
      if (tokenElement) {
        event.detail.headers['X-CSRFToken'] = tokenElement.value;
      }
    });
  </script>
  {% block scripts %}{% endblock %}
</body>
</html>

```

## planner/templates/planner/landing.html
```
{% extends "base.html" %}
{% load static planner_extras %}

{% block title %}TriPPlanner | Country Trip Planner{% endblock %}

{% block content %}
<section
  x-data="{
    idx: 0,
    images: [{% for img in hero_images %}'{% if img|is_external %}{{ img }}{% else %}{% static img %}{% endif %}'{% if not forloop.last %}, {% endif %}{% endfor %}],
    tick() { this.idx = (this.idx + 1) % this.images.length; }
  }"
  x-init="setInterval(() => tick(), 5000)"
  class="relative overflow-hidden rounded-3xl border border-white/40 bg-gradient-to-r from-ocean via-ink to-[#08354B] p-10 text-white shadow-2xl"
>
  <img :src="images[idx]" alt="Destination hero" class="absolute inset-0 h-full w-full object-cover opacity-25 transition-all duration-700">
  <div class="relative z-10 grid gap-10 lg:grid-cols-2">
    <div class="space-y-5">
      <p class="inline-flex rounded-full bg-white/20 px-4 py-1 text-xs font-semibold uppercase tracking-wide">Live Flight + Hotel Packaging</p>
      <h1 class="text-4xl font-extrabold leading-tight lg:text-5xl">Plan a country trip with ranked estimates and outbound links only.</h1>
      <p class="max-w-xl text-base text-white/90">
        TriPPlanner compares destination cities, estimates package ranges, explains the score, and sends you to partner sites for flights, hotels, and activities.
      </p>
      <div class="flex flex-wrap gap-3">
        {% if request.user.is_authenticated %}
          <a href="{% url 'planner:wizard' %}" class="rounded-xl bg-white px-6 py-3 font-semibold text-ink hover:bg-sky">Start Planning</a>
        {% else %}
          <a href="{% url 'signup' %}" class="rounded-xl bg-white px-6 py-3 font-semibold text-ink hover:bg-sky">Create Free Account</a>
          <a href="{% url 'login' %}" class="rounded-xl border border-white/60 px-6 py-3 font-semibold hover:bg-white/10">Login</a>
        {% endif %}
      </div>
    </div>
    <div class="grid gap-4 sm:grid-cols-2">
      <div class="rounded-2xl bg-white/12 p-4 backdrop-blur">
        <p class="text-xs uppercase tracking-wide text-sky">Async Pipeline</p>
        <p class="mt-2 text-lg font-bold">Celery fan-out by destination</p>
      </div>
      <div class="rounded-2xl bg-white/12 p-4 backdrop-blur">
        <p class="text-xs uppercase tracking-wide text-sky">Transparent ranking</p>
        <p class="mt-2 text-lg font-bold">Price + convenience + quality scores</p>
      </div>
      <div class="rounded-2xl bg-white/12 p-4 backdrop-blur">
        <p class="text-xs uppercase tracking-wide text-sky">Links-only engine</p>
        <p class="mt-2 text-lg font-bold">No checkout, no booking writes</p>
      </div>
      <div class="rounded-2xl bg-white/12 p-4 backdrop-blur">
        <p class="text-xs uppercase tracking-wide text-sky">Shareable links</p>
        <p class="mt-2 text-lg font-bold">Public tokenized result pages</p>
      </div>
    </div>
  </div>
</section>

<section class="mt-8 grid gap-4 md:grid-cols-4">
  <div class="rounded-2xl border border-ink/10 bg-white p-5 shadow-sm">
    <h3 class="font-bold">Travelpayouts</h3>
    <p class="mt-2 text-sm {% if providers.travelpayouts_enabled and providers.travelpayouts_token_configured %}text-green-700{% else %}text-amber-700{% endif %}">
      {% if providers.travelpayouts_enabled and providers.travelpayouts_token_configured %}Data API enabled{% else %}Running deterministic fallback estimates{% endif %}
    </p>
  </div>
  <div class="rounded-2xl border border-ink/10 bg-white p-5 shadow-sm">
    <h3 class="font-bold">Links-Only Mode</h3>
    <p class="mt-2 text-sm {% if providers.links_only_enabled %}text-green-700{% else %}text-amber-700{% endif %}">
      {% if providers.links_only_enabled %}Enabled: outbound links only{% else %}Disabled (legacy providers may run){% endif %}
    </p>
  </div>
  <div class="rounded-2xl border border-ink/10 bg-white p-5 shadow-sm">
    <h3 class="font-bold">Trust Layer</h3>
    <p class="mt-2 text-sm text-ink/70">Rate-limited start endpoint, Redis cache, correlation IDs, and persistent plan history.</p>
  </div>
  <div class="rounded-2xl border border-ink/10 bg-white p-5 shadow-sm">
    <h3 class="font-bold">FX Rates</h3>
    <p class="mt-2 text-sm {% if providers.fx_enabled %}text-green-700{% else %}text-amber-700{% endif %}">
      {% if providers.fx_enabled %}Live FX normalization enabled{% else %}FX not configured, using 1:1 fallback{% endif %}
    </p>
  </div>
</section>
{% endblock %}

```

## planner/templates/planner/planner_wizard.html
```
{% extends "base.html" %}

{% block title %}Planner Wizard | TriPPlanner{% endblock %}

{% block content %}
<section class="mb-6">
  <h1 class="text-3xl font-extrabold">Plan a Country Trip</h1>
  <p class="mt-2 text-sm text-ink/70">TriPPlanner will rank links-only package estimates across destination cities and return outbound flight/hotel links.</p>
</section>

{% if not providers.travelpayouts_enabled or not providers.travelpayouts_token_configured %}
  <div class="mb-6 rounded-2xl border border-amber-300 bg-amber-50 p-4 text-sm text-amber-900">
    Travelpayouts token is not configured. Planner will use deterministic estimates and still return tracked outbound links.
  </div>
{% endif %}
{% if not providers.links_only_enabled %}
  <div class="mb-6 rounded-2xl border border-amber-300 bg-amber-50 p-4 text-sm text-amber-900">
    TRIPPILOT_LINKS_ONLY is disabled. This deployment is designed for links-only mode; enable it for production.
  </div>
{% endif %}
{% if not providers.fx_enabled %}
  <div class="mb-6 rounded-2xl border border-amber-300 bg-amber-50 p-4 text-sm text-amber-900">
    FX not configured. Ranking still works with 1:1 fallback conversion.
  </div>
{% endif %}

<form method="post" x-data="{ step: 1 }" class="space-y-6">
  {% csrf_token %}

  <div class="grid gap-3 rounded-2xl border border-ink/10 bg-white p-4 text-xs font-semibold uppercase tracking-wider text-ink/50 sm:grid-cols-3">
    <button type="button" @click="step = 1" :class="step===1 ? 'text-ink' : ''">1. Route & Dates</button>
    <button type="button" @click="step = 2" :class="step===2 ? 'text-ink' : ''">2. Trip Budget</button>
    <button type="button" @click="step = 3" :class="step===3 ? 'text-ink' : ''">3. Filters</button>
  </div>

  <section x-show="step === 1" x-transition class="grid gap-4 rounded-3xl border border-ink/10 bg-white p-6 shadow-sm sm:grid-cols-2">
    <div>
      <label class="mb-1 block text-sm font-semibold">Origin (IATA or city)</label>
      <div class="rounded-xl border border-ink/10 px-3 py-2">{{ form.origin_input }}</div>
      {% if form.origin_input.errors %}<p class="mt-1 text-xs text-rose-600">{{ form.origin_input.errors|striptags }}</p>{% endif %}
    </div>
    <div>
      <label class="mb-1 block text-sm font-semibold">Destination Country (ISO-2)</label>
      <div class="rounded-xl border border-ink/10 px-3 py-2">{{ form.destination_country }}</div>
      {% if form.destination_country.errors %}<p class="mt-1 text-xs text-rose-600">{{ form.destination_country.errors|striptags }}</p>{% endif %}
    </div>
    <div>
      <label class="mb-1 block text-sm font-semibold">Date mode</label>
      <div class="rounded-xl border border-ink/10 px-3 py-2">{{ form.date_mode }}</div>
    </div>
    <div>
      <label class="mb-1 block text-sm font-semibold">Travel month (for flexible mode)</label>
      <div class="rounded-xl border border-ink/10 px-3 py-2">{{ form.travel_month }}</div>
      {% if form.travel_month.errors %}<p class="mt-1 text-xs text-rose-600">{{ form.travel_month.errors|striptags }}</p>{% endif %}
    </div>
    <div>
      <label class="mb-1 block text-sm font-semibold">Departure date</label>
      <div class="rounded-xl border border-ink/10 px-3 py-2">{{ form.depart_date }}</div>
      {% if form.depart_date.errors %}<p class="mt-1 text-xs text-rose-600">{{ form.depart_date.errors|striptags }}</p>{% endif %}
    </div>
    <div>
      <label class="mb-1 block text-sm font-semibold">Return date</label>
      <div class="rounded-xl border border-ink/10 px-3 py-2">{{ form.return_date }}</div>
      {% if form.return_date.errors %}<p class="mt-1 text-xs text-rose-600">{{ form.return_date.errors|striptags }}</p>{% endif %}
    </div>
    <div>
      <label class="mb-1 block text-sm font-semibold">Flexibility (+/- days)</label>
      <div class="rounded-xl border border-ink/10 px-3 py-2">{{ form.flexibility_days }}</div>
    </div>
  </section>

  <section x-show="step === 2" x-transition class="grid gap-4 rounded-3xl border border-ink/10 bg-white p-6 shadow-sm sm:grid-cols-2">
    <div>
      <label class="mb-1 block text-sm font-semibold">Trip nights min</label>
      <div class="rounded-xl border border-ink/10 px-3 py-2">{{ form.nights_min }}</div>
    </div>
    <div>
      <label class="mb-1 block text-sm font-semibold">Trip nights max</label>
      <div class="rounded-xl border border-ink/10 px-3 py-2">{{ form.nights_max }}</div>
      {% if form.nights_max.errors %}<p class="mt-1 text-xs text-rose-600">{{ form.nights_max.errors|striptags }}</p>{% endif %}
    </div>
    <div>
      <label class="mb-1 block text-sm font-semibold">Total budget</label>
      <div class="rounded-xl border border-ink/10 px-3 py-2">{{ form.total_budget }}</div>
    </div>
    <div>
      <label class="mb-1 block text-sm font-semibold">Travelers</label>
      <div class="rounded-xl border border-ink/10 px-3 py-2">{{ form.travelers }}</div>
    </div>
    <div>
      <label class="mb-1 block text-sm font-semibold">Currency</label>
      <div class="rounded-xl border border-ink/10 px-3 py-2">{{ form.currency }}</div>
    </div>
  </section>

  <section x-show="step === 3" x-transition class="grid gap-4 rounded-3xl border border-ink/10 bg-white p-6 shadow-sm sm:grid-cols-2">
    <div>
      <label class="mb-1 block text-sm font-semibold">Cabin</label>
      <div class="rounded-xl border border-ink/10 px-3 py-2">{{ form.cabin }}</div>
    </div>
    <div>
      <label class="mb-1 block text-sm font-semibold">Flight max stops</label>
      <div class="rounded-xl border border-ink/10 px-3 py-2">{{ form.flight_max_stops }}</div>
    </div>
    <div>
      <label class="mb-1 block text-sm font-semibold">Flight max duration (minutes)</label>
      <div class="rounded-xl border border-ink/10 px-3 py-2">{{ form.flight_max_duration_minutes }}</div>
    </div>
    <div>
      <label class="mb-1 block text-sm font-semibold">Hotel stars minimum</label>
      <div class="rounded-xl border border-ink/10 px-3 py-2">{{ form.hotel_stars_min }}</div>
    </div>
    <div>
      <label class="mb-1 block text-sm font-semibold">Hotel guest rating minimum</label>
      <div class="rounded-xl border border-ink/10 px-3 py-2">{{ form.hotel_guest_rating_min }}</div>
    </div>
    <div class="sm:col-span-2">
      <label class="mb-1 block text-sm font-semibold">Amenities</label>
      <div class="grid gap-2 rounded-xl border border-ink/10 px-3 py-3 sm:grid-cols-3">{{ form.hotel_amenities }}</div>
    </div>
    <div class="sm:col-span-2">
      <label class="mb-1 block text-sm font-semibold">Preferences (impacts preference-match score)</label>
      <div class="grid gap-2 rounded-xl border border-ink/10 px-3 py-3 sm:grid-cols-3">{{ form.preferences }}</div>
    </div>
  </section>

  {% if form.non_field_errors %}
    <div class="rounded-xl border border-rose-300 bg-rose-50 p-3 text-sm text-rose-700">{{ form.non_field_errors|striptags }}</div>
  {% endif %}

  <div class="flex items-center justify-between">
    <button type="button" @click="step = Math.max(1, step - 1)" class="rounded-xl border border-ink/20 bg-white px-5 py-3 font-semibold hover:bg-sky/30">Back</button>
    <div class="flex gap-3">
      <button type="button" @click="step = Math.min(3, step + 1)" class="rounded-xl border border-ink/20 bg-white px-5 py-3 font-semibold hover:bg-sky/30">Next</button>
      <button type="submit" class="rounded-xl bg-ink px-6 py-3 font-semibold text-white hover:bg-ocean">Start Links-Only Search</button>
    </div>
  </div>
</form>
{% endblock %}

```

## planner/templates/planner/results.html
```
{% extends "base.html" %}
{% load planner_extras %}

{% block title %}Plan {{ plan.id }} | TriPPlanner{% endblock %}

{% block content %}
<section class="mb-6 rounded-3xl border border-ink/10 bg-white p-6 shadow-sm">
  <div class="flex flex-wrap items-center justify-between gap-4">
    <div>
      <h1 class="text-3xl font-extrabold">Links-Only Trip Packages</h1>
      <p class="mt-1 text-sm text-ink/70">
        {{ plan.origin_code }} to {{ plan.destination_country }} | Budget {{ plan.total_budget|money:plan.search_currency }} | Travelers {{ plan.travelers }} | No booking on TriPPlanner
      </p>
    </div>
    <div class="flex flex-wrap gap-2">
      <a href="{% url 'planner:share' plan.public_token %}" class="rounded-xl border border-ink/15 bg-white px-4 py-2 text-sm font-semibold hover:bg-sky/30">Share link</a>
      <a href="{% url 'planner:wizard' %}" class="rounded-xl bg-ink px-4 py-2 text-sm font-semibold text-white hover:bg-ocean">New search</a>
    </div>
  </div>
</section>

{% if (not providers.travelpayouts_enabled) or (not providers.links_only_enabled) or (not providers.fx_enabled) %}
  <div class="mb-6 rounded-2xl border border-amber-300 bg-amber-50 p-4 text-sm text-amber-900">
    Travelpayouts links-only mode is not fully configured. Fallback estimates still run, but data freshness and confidence can decrease.
  </div>
{% endif %}

<div x-data="{ compare: [] }" class="space-y-4">
  <div class="sticky top-2 z-20 rounded-2xl border border-ink/10 bg-white/95 p-4 backdrop-blur">
    <div class="flex flex-wrap items-center justify-between gap-3">
      <div class="flex items-center gap-2">
        <span class="text-sm font-semibold">Sort</span>
        <select
          name="sort"
          class="rounded-lg border border-ink/20 px-3 py-2 text-sm"
          hx-get="{% url 'planner:packages-partial' plan.id %}"
          hx-target="#package-cards"
          hx-include="[name='sort'], [name='token']"
          hx-trigger="change"
        >
          <option value="best_value" {% if sort_mode == 'best_value' %}selected{% endif %}>Best value</option>
          <option value="cheapest" {% if sort_mode == 'cheapest' %}selected{% endif %}>Cheapest</option>
          <option value="fastest" {% if sort_mode == 'fastest' %}selected{% endif %}>Fastest</option>
          <option value="best_hotel" {% if sort_mode == 'best_hotel' %}selected{% endif %}>Best hotel</option>
        </select>
        {% if read_only %}
          <input type="hidden" name="token" value="{{ plan.public_token }}">
        {% endif %}
        {% if not read_only %}
          <button
            id="refresh-top-prices"
            type="button"
            class="rounded-lg border border-ink/20 bg-white px-3 py-2 text-xs font-semibold hover:bg-sky/30"
          >Refresh top prices</button>
          <span id="refresh-top-status" class="text-xs text-ink/60"></span>
        {% endif %}
      </div>
      <div class="text-xs text-ink/60">Public token: {{ plan.public_token }}</div>
    </div>
  </div>

  <div
    id="progress-panel"
    hx-get="{% url 'planner:progress-partial' plan.id %}{% if read_only %}?token={{ plan.public_token }}{% endif %}"
    hx-trigger="load, every 2s"
    hx-target="this"
    hx-swap="outerHTML"
  ></div>

  <div
    id="package-cards"
    hx-get="{% url 'planner:packages-partial' plan.id %}?sort={{ sort_mode }}{% if read_only %}&token={{ plan.public_token }}{% endif %}"
    hx-trigger="load, every 4s"
    hx-target="this"
    hx-swap="innerHTML"
  ></div>

  <aside x-show="compare.length > 0" class="fixed bottom-4 left-1/2 z-30 w-[95%] max-w-3xl -translate-x-1/2 rounded-2xl border border-ink/20 bg-white p-4 shadow-xl">
    <div class="flex items-center justify-between">
      <p class="text-sm font-semibold">Compare drawer (<span x-text="compare.length"></span>)</p>
      <button @click="compare = []" class="text-xs font-semibold text-ink/60 hover:text-ink">Clear</button>
    </div>
    <div class="mt-2 flex flex-wrap gap-2 text-xs">
      <template x-for="id in compare" :key="id">
        <span class="rounded-full bg-sky px-3 py-1 font-semibold text-ink" x-text="id.slice(0, 8)"></span>
      </template>
    </div>
  </aside>
</div>
{% endblock %}

{% block scripts %}
  <script>
    (function () {
      function getCookie(name) {
        const value = `; ${document.cookie}`;
        const parts = value.split(`; ${name}=`);
        if (parts.length === 2) {
          return parts.pop().split(';').shift();
        }
        return '';
      }

      const csrfToken = getCookie('csrftoken');

      document.body.addEventListener('click', function (event) {
        const link = event.target.closest('.track-click');
        if (!link) return;
        fetch('/api/click', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'X-CSRFToken': csrfToken
          },
          body: JSON.stringify({
            provider: link.dataset.provider || 'unknown',
            link_type: link.dataset.linkType || 'other',
            destination: link.dataset.destination || '',
            correlation_id: link.dataset.correlationId || '',
            plan_id: link.dataset.planId || null,
            package_id: link.dataset.packageId || null,
            outbound_url: link.href
          }),
          keepalive: true
        }).catch(() => {});
      });

      const refreshButton = document.getElementById('refresh-top-prices');
      const refreshStatus = document.getElementById('refresh-top-status');
      if (refreshButton) {
        refreshButton.addEventListener('click', function () {
          refreshStatus.textContent = 'Refreshing...';
          fetch('/api/plans/{{ plan.id }}/refresh', {
            method: 'POST',
            headers: {
              'Content-Type': 'application/json',
              'X-CSRFToken': csrfToken
            }
          })
            .then((response) => response.json())
            .then(() => {
              refreshStatus.textContent = 'Queued';
            })
            .catch(() => {
              refreshStatus.textContent = 'Failed';
            });
        });
      }

      let leafletLoader = null;
      function ensureLeaflet() {
        if (window.L) return Promise.resolve();
        if (leafletLoader) return leafletLoader;
        leafletLoader = new Promise((resolve, reject) => {
          const style = document.createElement('link');
          style.rel = 'stylesheet';
          style.href = 'https://unpkg.com/leaflet@1.9.4/dist/leaflet.css';
          document.head.appendChild(style);

          const script = document.createElement('script');
          script.src = 'https://unpkg.com/leaflet@1.9.4/dist/leaflet.js';
          script.onload = resolve;
          script.onerror = reject;
          document.head.appendChild(script);
        });
        return leafletLoader;
      }

      function initMap(container) {
        if (container.dataset.ready === '1') return;
        ensureLeaflet().then(() => {
          const hotelLat = parseFloat(container.dataset.hotelLat);
          const hotelLng = parseFloat(container.dataset.hotelLng);
          const cityLat = parseFloat(container.dataset.cityLat);
          const cityLng = parseFloat(container.dataset.cityLng);
          const map = L.map(container).setView([hotelLat, hotelLng], 12);
          L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
            maxZoom: 18,
            attribution: '&copy; OpenStreetMap contributors'
          }).addTo(map);
          L.marker([hotelLat, hotelLng]).addTo(map).bindPopup(container.dataset.hotelName || 'Hotel');
          L.marker([cityLat, cityLng]).addTo(map).bindPopup(container.dataset.cityName || 'City center');
          const bounds = L.latLngBounds([[hotelLat, hotelLng], [cityLat, cityLng]]);
          map.fitBounds(bounds.pad(0.3));
          container.dataset.ready = '1';
        }).catch(() => {});
      }

      document.body.addEventListener('toggle', function (event) {
        const details = event.target.closest('.map-toggle');
        if (!details || !details.open) return;
        const container = details.querySelector('.leaflet-map');
        if (container) initMap(container);
      }, true);
    })();
  </script>
{% endblock %}

```

## planner/templates/planner/partials/package_cards.html
```
{% load planner_extras %}

{% if plan.status != "completed" %}
  <div class="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
    {% for _ in "123456"|make_list %}
      <article class="animate-pulse rounded-2xl border border-ink/10 bg-white p-5 shadow-sm">
        <div class="h-4 w-2/3 rounded bg-sky/40"></div>
        <div class="mt-3 h-3 w-1/2 rounded bg-sky/30"></div>
        <div class="mt-6 h-20 rounded-xl bg-sky/20"></div>
        <div class="mt-4 h-8 rounded bg-sky/30"></div>
      </article>
    {% endfor %}
  </div>
{% elif not packages %}
  <div class="rounded-2xl border border-ink/10 bg-white p-6 text-sm text-ink/70">No packages available for this plan yet.</div>
{% else %}
  <div class="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
    {% for package in packages %}
      <article class="rounded-2xl border border-ink/10 bg-white p-5 shadow-sm transition hover:-translate-y-0.5 hover:shadow-md">
        <div class="flex items-start justify-between gap-3">
          <div>
            <p class="text-xs font-semibold uppercase tracking-wider text-ink/50">{{ package.candidate.city_name }}</p>
            <h3 class="text-xl font-extrabold">
              {{ package.estimated_total_min|money:package.currency }} - {{ package.estimated_total_max|money:package.currency }}
            </h3>
            <p class="text-xs text-ink/60">Score {{ package.score }} | Rank #{{ package.rank }}</p>
          </div>
          <label class="inline-flex items-center gap-2 text-xs font-semibold text-ink/60">
            <input
              type="checkbox"
              class="rounded border-ink/20"
              @change="
                if ($event.target.checked) { if (!compare.includes('{{ package.id }}')) compare.push('{{ package.id }}'); }
                else { compare = compare.filter(item => item !== '{{ package.id }}'); }
              "
            >
            Compare
          </label>
        </div>

        <div class="mt-4 space-y-3 rounded-xl bg-sky/20 p-3 text-sm">
          <div>
            <p class="font-semibold">Flight</p>
            <p class="text-xs text-ink/70">
              {{ package.flight_option.origin_airport }} -> {{ package.flight_option.destination_airport }} |
              {{ package.flight_option.duration_minutes|duration_hm }} | Stops {{ package.flight_option.stops }}
            </p>
            <p class="text-xs text-ink/70">
              Estimate {{ package.estimated_flight_min|money:package.currency }} - {{ package.estimated_flight_max|money:package.currency }}
            </p>
            <p class="text-xs text-ink/55">Freshness {{ package.freshness_at|minutes_ago }}</p>
            <a
              href="{{ package.flight_url|default:package.flight_option.deeplink_url }}"
              target="_blank"
              rel="noopener"
              class="track-click mt-2 inline-flex rounded-lg bg-ocean px-3 py-1.5 text-xs font-semibold text-white hover:bg-ink"
              data-provider="{{ package.flight_option.provider }}"
              data-link-type="flight"
              data-destination="{{ package.candidate.city_name }}-{{ package.candidate.country_code }}"
              data-correlation-id="{{ package.plan_id }}:{{ package.id }}:flight"
              data-plan-id="{{ package.plan_id }}"
              data-package-id="{{ package.id }}"
            >See flights</a>
          </div>
          <div class="border-t border-ink/10 pt-2">
            <p class="font-semibold">Hotel</p>
            <p class="text-xs text-ink/70">{{ package.hotel_option.name }}</p>
            <p class="text-xs text-ink/70">Rating {{ package.hotel_option.star_rating }}* / Guest {{ package.hotel_option.guest_rating }}</p>
            <p class="text-xs text-ink/70">
              Nightly estimate {{ package.estimated_hotel_nightly_min|money:package.currency }} - {{ package.estimated_hotel_nightly_max|money:package.currency }}
            </p>
            <p class="text-xs text-ink/55">Freshness {{ package.freshness_at|minutes_ago }}</p>
            {% if package.hotel_option.distance_km %}
              <p class="text-xs text-ink/55">Distance to center: {{ package.hotel_option.distance_km|floatformat:1 }} km</p>
            {% endif %}
            <a
              href="{{ package.hotel_url|default:package.hotel_option.deeplink_url }}"
              target="_blank"
              rel="noopener"
              class="track-click mt-2 inline-flex rounded-lg bg-ocean px-3 py-1.5 text-xs font-semibold text-white hover:bg-ink"
              data-provider="{{ package.hotel_option.provider }}"
              data-link-type="hotel"
              data-destination="{{ package.candidate.city_name }}-{{ package.candidate.country_code }}"
              data-correlation-id="{{ package.plan_id }}:{{ package.id }}:hotel"
              data-plan-id="{{ package.plan_id }}"
              data-package-id="{{ package.id }}"
            >See hotels</a>
            {% if package.tours_url %}
              <a
                href="{{ package.tours_url }}"
                target="_blank"
                rel="noopener"
                class="track-click mt-2 inline-flex rounded-lg border border-ink/20 bg-white px-3 py-1.5 text-xs font-semibold text-ink hover:bg-sky/30"
                data-provider="{{ package.hotel_option.provider }}"
                data-link-type="tour"
                data-destination="{{ package.candidate.city_name }}-{{ package.candidate.country_code }}"
                data-correlation-id="{{ package.plan_id }}:{{ package.id }}:tour"
                data-plan-id="{{ package.plan_id }}"
                data-package-id="{{ package.id }}"
              >See tours</a>
            {% endif %}
          </div>
        </div>

        <details class="mt-3 rounded-lg border border-ink/10 bg-white/70 p-2">
          <summary class="cursor-pointer text-xs font-semibold text-ink">Why this plan</summary>
          <div class="mt-2 space-y-1 text-xs text-ink/70">
            {% for note in package.score_breakdown.explanations %}
              <div>{{ note }}</div>
            {% endfor %}
            <div class="pt-1 font-semibold text-ink/80">
              Price {{ package.price_score }} | Convenience {{ package.convenience_score }} | Quality {{ package.quality_score }} | Location {{ package.location_score }}
            </div>
          </div>
        </details>

        {% if package.hotel_option.latitude and package.hotel_option.longitude and package.candidate.latitude and package.candidate.longitude %}
          <details class="mt-3 rounded-lg border border-ink/10 bg-white/70 p-2 map-toggle">
            <summary class="cursor-pointer text-xs font-semibold text-ink">Map preview</summary>
            <div
              class="leaflet-map mt-2 h-40 rounded-lg"
              data-map-id="map-{{ package.id }}"
              data-hotel-lat="{{ package.hotel_option.latitude }}"
              data-hotel-lng="{{ package.hotel_option.longitude }}"
              data-city-lat="{{ package.candidate.latitude }}"
              data-city-lng="{{ package.candidate.longitude }}"
              data-hotel-name="{{ package.hotel_option.name }}"
              data-city-name="{{ package.candidate.city_name }}"
            ></div>
          </details>
        {% endif %}

        <div class="mt-4 flex flex-wrap gap-2">
          {% for note in package.explanations %}
            <span class="rounded-full bg-mint/20 px-2 py-1 text-xs font-semibold text-ink">{{ note }}</span>
          {% endfor %}
        </div>

        <div class="mt-4 flex items-center justify-between">
          <div class="text-xs text-ink/60">Package scored {{ package.last_scored_at|minutes_ago }}</div>
          {% if package.id in saved_ids %}
            {% include "planner/partials/save_button.html" with package=package is_saved=True read_only=read_only %}
          {% else %}
            {% include "planner/partials/save_button.html" with package=package is_saved=False read_only=read_only %}
          {% endif %}
        </div>
      </article>
    {% endfor %}
  </div>
{% endif %}

```

## planner/static/trippilot/app.css
```
:root {
  --ink: #102338;
  --ocean: #1e4967;
  --mint: #5fa8a6;
  --sky: #cae9ff;
  --sand: #fff7e8;
  --glass: rgba(255, 255, 255, 0.72);
  --shadow-soft: 0 12px 30px rgba(16, 35, 56, 0.12);
  --shadow-strong: 0 18px 46px rgba(16, 35, 56, 0.2);
}

* {
  box-sizing: border-box;
}

body {
  margin: 0;
  color: var(--ink);
  font-family: "Manrope", sans-serif;
  min-height: 100vh;
  background:
    radial-gradient(circle at 18% 18%, rgba(255, 255, 255, 0.95), rgba(255, 255, 255, 0.2) 44%, transparent 72%),
    radial-gradient(circle at 84% 14%, rgba(202, 233, 255, 0.8), transparent 40%),
    linear-gradient(120deg, #fff8eb, #f2f7ff 35%, #e8f5f3 68%, #fff8ed);
  background-size: 200% 200%;
  animation: gradientDrift 14s ease-in-out infinite;
}

h1,
h2,
h3,
h4,
h5,
h6,
.display-font {
  font-family: "Outfit", sans-serif;
}

a {
  color: inherit;
}

input[type="text"],
input[type="email"],
input[type="password"],
input[type="number"],
input[type="date"],
input[type="month"],
select,
textarea {
  width: 100%;
  border: 0;
  background: transparent;
  color: var(--ink);
  outline: none;
  font-size: 0.95rem;
}

select[multiple] {
  min-height: 5rem;
}

input[type="checkbox"] {
  accent-color: var(--ocean);
}

.animate-pulse {
  animation: shimmer 1.6s infinite linear;
}

.rounded-2xl,
.rounded-3xl,
.rounded-xl {
  backdrop-filter: blur(10px);
}

.bg-white,
.bg-white\/70,
.bg-white\/80,
.bg-white\/95,
.bg-white\/12 {
  background-color: var(--glass) !important;
}

.shadow-sm,
.shadow-md {
  box-shadow: var(--shadow-soft) !important;
}

.shadow-xl,
.shadow-2xl {
  box-shadow: var(--shadow-strong) !important;
}

.track-click,
button,
[role="button"] {
  transition: transform 0.18s ease, box-shadow 0.18s ease, filter 0.18s ease;
}

.track-click:hover,
button:hover,
[role="button"]:hover {
  transform: translateY(-1px);
  filter: saturate(1.08);
}

.track-click:active,
button:active,
[role="button"]:active {
  transform: translateY(0);
}

button[type="submit"],
.bg-ink,
.bg-ocean {
  position: relative;
  overflow: hidden;
}

button[type="submit"]::after,
.bg-ink::after,
.bg-ocean::after {
  content: "";
  position: absolute;
  inset: -180% auto auto -60%;
  width: 45%;
  height: 360%;
  transform: rotate(22deg);
  background: linear-gradient(
    to right,
    rgba(255, 255, 255, 0),
    rgba(255, 255, 255, 0.42),
    rgba(255, 255, 255, 0)
  );
  animation: buttonShimmer 2.8s linear infinite;
  pointer-events: none;
}

.leaflet-map {
  border: 1px solid rgba(16, 35, 56, 0.08);
  box-shadow: inset 0 0 0 1px rgba(255, 255, 255, 0.3);
}

@keyframes shimmer {
  0% {
    opacity: 0.42;
  }
  50% {
    opacity: 1;
  }
  100% {
    opacity: 0.42;
  }
}

@keyframes gradientDrift {
  0% {
    background-position: 0% 50%;
  }
  50% {
    background-position: 100% 50%;
  }
  100% {
    background-position: 0% 50%;
  }
}

@keyframes buttonShimmer {
  0% {
    left: -65%;
  }
  100% {
    left: 150%;
  }
}

```

## planner/data/pricing_baselines.json
```
{
  "distance_bands_km": [
    {"band": "short", "max_km": 900, "flight_min": 120, "flight_max": 320, "travel_time_hours": 2.2, "nonstop_likelihood": 0.9},
    {"band": "medium", "max_km": 2800, "flight_min": 260, "flight_max": 680, "travel_time_hours": 5.0, "nonstop_likelihood": 0.72},
    {"band": "long", "max_km": 6200, "flight_min": 520, "flight_max": 1250, "travel_time_hours": 9.0, "nonstop_likelihood": 0.45},
    {"band": "ultra_long", "max_km": 30000, "flight_min": 850, "flight_max": 1900, "travel_time_hours": 14.0, "nonstop_likelihood": 0.25}
  ],
  "hotel_tiers": {
    "budget": {"nightly_min": 45, "nightly_max": 110, "star_rating": 3.0, "guest_rating": 7.3},
    "standard": {"nightly_min": 80, "nightly_max": 190, "star_rating": 3.6, "guest_rating": 8.0},
    "premium": {"nightly_min": 145, "nightly_max": 340, "star_rating": 4.2, "guest_rating": 8.5},
    "luxury": {"nightly_min": 260, "nightly_max": 620, "star_rating": 4.7, "guest_rating": 9.0}
  },
  "season_multipliers": {
    "1": 0.9,
    "2": 0.9,
    "3": 0.96,
    "4": 1.0,
    "5": 1.08,
    "6": 1.15,
    "7": 1.2,
    "8": 1.18,
    "9": 1.03,
    "10": 0.99,
    "11": 0.94,
    "12": 1.04
  },
  "country_defaults": {
    "AU": {"tier": "premium", "tags": ["beach", "nature", "food"], "nonstop_likelihood": 0.42},
    "BR": {"tier": "standard", "tags": ["beach", "nightlife", "nature", "food"], "nonstop_likelihood": 0.38},
    "CA": {"tier": "premium", "tags": ["nature", "culture", "food", "quiet"], "nonstop_likelihood": 0.58},
    "DE": {"tier": "premium", "tags": ["culture", "food", "quiet"], "nonstop_likelihood": 0.62},
    "ES": {"tier": "premium", "tags": ["beach", "culture", "food", "nightlife"], "nonstop_likelihood": 0.64},
    "FR": {"tier": "premium", "tags": ["culture", "food", "nightlife"], "nonstop_likelihood": 0.66},
    "GB": {"tier": "premium", "tags": ["culture", "food", "nightlife"], "nonstop_likelihood": 0.69},
    "GR": {"tier": "standard", "tags": ["beach", "culture", "food", "quiet"], "nonstop_likelihood": 0.5},
    "IT": {"tier": "premium", "tags": ["culture", "food", "beach", "nightlife"], "nonstop_likelihood": 0.61},
    "JP": {"tier": "premium", "tags": ["culture", "food", "nightlife", "quiet"], "nonstop_likelihood": 0.47},
    "MX": {"tier": "standard", "tags": ["beach", "food", "nightlife", "culture"], "nonstop_likelihood": 0.56},
    "NL": {"tier": "premium", "tags": ["culture", "nightlife", "food"], "nonstop_likelihood": 0.71},
    "PT": {"tier": "standard", "tags": ["beach", "culture", "food", "quiet"], "nonstop_likelihood": 0.59},
    "TH": {"tier": "budget", "tags": ["beach", "food", "nightlife", "nature"], "nonstop_likelihood": 0.36},
    "US": {"tier": "premium", "tags": ["nature", "culture", "nightlife", "food"], "nonstop_likelihood": 0.74}
  },
  "airport_overrides": {
    "CDG": {"tier": "premium", "tags": ["culture", "food", "nightlife"]},
    "NCE": {"tier": "premium", "tags": ["beach", "food", "quiet"]},
    "LHR": {"tier": "premium", "tags": ["culture", "nightlife", "food"]},
    "ATH": {"tier": "standard", "tags": ["beach", "culture", "food"]},
    "JTR": {"tier": "luxury", "tags": ["beach", "quiet", "culture"]},
    "HND": {"tier": "premium", "tags": ["culture", "food", "nightlife", "quiet"]},
    "KIX": {"tier": "standard", "tags": ["culture", "food", "nightlife"]},
    "CUN": {"tier": "standard", "tags": ["beach", "nightlife", "food"]},
    "LIS": {"tier": "standard", "tags": ["culture", "food", "beach"]},
    "BKK": {"tier": "budget", "tags": ["food", "nightlife", "culture"]},
    "HKT": {"tier": "standard", "tags": ["beach", "nightlife", "nature"]},
    "JFK": {"tier": "luxury", "tags": ["culture", "food", "nightlife"]},
    "LAX": {"tier": "premium", "tags": ["beach", "culture", "nightlife"]},
    "MIA": {"tier": "premium", "tags": ["beach", "nightlife", "food"]},
    "LAS": {"tier": "premium", "tags": ["nightlife", "food"]},
    "BCN": {"tier": "premium", "tags": ["beach", "culture", "food", "nightlife"]},
    "MAD": {"tier": "premium", "tags": ["culture", "food", "nightlife"]},
    "FCO": {"tier": "premium", "tags": ["culture", "food"]},
    "VCE": {"tier": "luxury", "tags": ["culture", "quiet", "food"]},
    "AMS": {"tier": "premium", "tags": ["culture", "nightlife", "food"]}
  }
}

```

## trip_pilot/settings.py
```
import os
from pathlib import Path

import sentry_sdk

BASE_DIR = Path(__file__).resolve().parent.parent


def env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "dev-only-unsafe-secret-key")
DEBUG = env_bool("DJANGO_DEBUG", default=False)
ALLOWED_HOSTS = [h.strip() for h in os.getenv("DJANGO_ALLOWED_HOSTS", "").split(",") if h.strip()] or ["localhost", "127.0.0.1"]
CSRF_TRUSTED_ORIGINS = [
    origin.strip()
    for origin in os.getenv("DJANGO_CSRF_TRUSTED_ORIGINS", "").split(",")
    if origin.strip()
]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "csp",
    "planner.apps.PlannerConfig",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "planner.middleware.RequestContextMiddleware",
    "csp.middleware.CSPMiddleware",
]

ROOT_URLCONF = "trip_pilot.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "planner" / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "trip_pilot.wsgi.application"
ASGI_APPLICATION = "trip_pilot.asgi.application"

POSTGRES_DB = os.getenv("POSTGRES_DB")
if POSTGRES_DB:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": POSTGRES_DB,
            "USER": os.getenv("POSTGRES_USER", "trippilot"),
            "PASSWORD": os.getenv("POSTGRES_PASSWORD", "trippilot"),
            "HOST": os.getenv("POSTGRES_HOST", "postgres"),
            "PORT": os.getenv("POSTGRES_PORT", "5432"),
            "CONN_MAX_AGE": 60,
        },
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        },
    }

CACHE_URL = os.getenv("REDIS_URL")
if CACHE_URL:
    CACHES = {
        "default": {
            "BACKEND": "django_redis.cache.RedisCache",
            "LOCATION": CACHE_URL,
            "OPTIONS": {
                "CLIENT_CLASS": "django_redis.client.DefaultClient",
            },
        },
    }
else:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "trippilot-local-cache",
        },
    }

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "planner" / "static"]
STATICFILES_STORAGE = "whitenoise.storage.CompressedManifestStaticFilesStorage"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LOGIN_REDIRECT_URL = "planner:wizard"
LOGOUT_REDIRECT_URL = "planner:landing"
LOGIN_URL = "login"

EMAIL_BACKEND = os.getenv("EMAIL_BACKEND", "django.core.mail.backends.console.EmailBackend")
DEFAULT_FROM_EMAIL = os.getenv("DEFAULT_FROM_EMAIL", "noreply@trippilot.local")

SESSION_COOKIE_SECURE = env_bool("SESSION_COOKIE_SECURE", default=not DEBUG)
CSRF_COOKIE_SECURE = env_bool("CSRF_COOKIE_SECURE", default=not DEBUG)
CSRF_COOKIE_HTTPONLY = True
SESSION_COOKIE_HTTPONLY = True
SECURE_SSL_REDIRECT = env_bool("SECURE_SSL_REDIRECT", default=False)
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"

CONTENT_SECURITY_POLICY = {
    "DIRECTIVES": {
        "default-src": ("'self'",),
        "script-src": (
            "'self'",
            "'unsafe-inline'",
            "https://cdn.tailwindcss.com",
            "https://unpkg.com",
            "https://cdn.jsdelivr.net",
        ),
        "style-src": ("'self'", "'unsafe-inline'", "https://fonts.googleapis.com"),
        "style-src-elem": ("'self'", "'unsafe-inline'", "https://fonts.googleapis.com", "https://unpkg.com"),
        "font-src": ("'self'", "https://fonts.gstatic.com", "data:"),
        "img-src": ("'self'", "data:", "https://images.unsplash.com", "https://*.tile.openstreetmap.org"),
        "connect-src": ("'self'", "https://api.unsplash.com"),
    },
}

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "DEFAULT_THROTTLE_CLASSES": [
        "rest_framework.throttling.UserRateThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": {
        "plan_start": "8/hour",
        "click_track": "240/hour",
    },
}

CELERY_BROKER_URL = os.getenv("CELERY_BROKER_URL", "redis://localhost:6379/1")
CELERY_RESULT_BACKEND = os.getenv("CELERY_RESULT_BACKEND", "redis://localhost:6379/2")
CELERY_TASK_ACKS_LATE = True
CELERY_TASK_REJECT_ON_WORKER_LOST = True
CELERY_TASK_TRACK_STARTED = True
CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP = True
CELERY_TIMEZONE = TIME_ZONE
CELERY_BEAT_SCHEDULE = {
    "cleanup-old-plans": {
        "task": "planner.tasks.cleanup_old_plans",
        "schedule": 60 * 60 * 24,
    },
    "refresh-fx-rates": {
        "task": "planner.tasks.refresh_fx_rates_daily",
        "schedule": 60 * 60 * 24,
    },
}

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "filters": {
        "request_context": {
            "()": "trip_pilot.logging.RequestContextFilter",
        },
    },
    "formatters": {
        "json": {
            "class": "pythonjsonlogger.jsonlogger.JsonFormatter",
            "format": "%(asctime)s %(levelname)s %(name)s %(message)s %(request_id)s %(plan_id)s",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "json",
            "filters": ["request_context"],
        },
    },
    "root": {
        "handlers": ["console"],
        "level": LOG_LEVEL,
    },
}

SENTRY_DSN = os.getenv("SENTRY_DSN")
if SENTRY_DSN:
    sentry_sdk.init(
        dsn=SENTRY_DSN,
        traces_sample_rate=float(os.getenv("SENTRY_TRACES_SAMPLE_RATE", "0.05")),
        send_default_pii=False,
        environment=os.getenv("SENTRY_ENVIRONMENT", "development" if DEBUG else "production"),
    )

```

## planner/tests/test_providers.py
```
from datetime import date
from unittest.mock import patch

import httpx
from django.core.cache import cache

from planner.services.travelpayouts.adapter import TravelpayoutsAdapter


class DummyResponse:
    def __init__(self, payload: dict, status_code: int = 200, url: str = "https://api.travelpayouts.com/test"):
        self._payload = payload
        self.status_code = status_code
        self.request = httpx.Request("GET", url)

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            response = httpx.Response(self.status_code, request=self.request)
            raise httpx.HTTPStatusError("Request failed", request=self.request, response=response)

    def json(self) -> dict:
        return self._payload


def _estimate_payload(adapter: TravelpayoutsAdapter):
    return adapter.estimate(
        origin_code="JFK",
        destination_code="CDG",
        destination_city="Paris",
        destination_country="FR",
        depart_date=date(2026, 7, 1),
        return_date=date(2026, 7, 8),
        travelers=2,
        tier="premium",
        tags=["culture", "food"],
        origin_coords=(40.6413, -73.7781),
        destination_coords=(49.0097, 2.5479),
        nonstop_likelihood=0.75,
        preferred_currency="USD",
    )


def test_travelpayouts_adapter_parses_live_prices(monkeypatch):
    cache.clear()
    monkeypatch.setenv("TRAVELPAYOUTS_ENABLED", "true")
    monkeypatch.setenv("TRAVELPAYOUTS_API_TOKEN", "token")
    monkeypatch.setenv("TRAVELPAYOUTS_BASE_CURRENCY", "USD")

    adapter = TravelpayoutsAdapter()
    responses = [
        DummyResponse({"data": {"CDG": {"0": {"price": 520}, "1": {"price": 590}}}}),
        DummyResponse({"data": [{"price": 540}, {"price": 610}], "meta": {"updated_at": "2026-06-01T10:00:00Z"}}),
        DummyResponse({"data": {"CDG": {"price": 560}}}),
    ]

    with patch("httpx.request", side_effect=responses):
        estimate = _estimate_payload(adapter)

    assert estimate.source == "travelpayouts"
    assert estimate.flight_min > 0
    assert estimate.flight_max >= estimate.flight_min
    assert estimate.endpoints["cheap"] == "ok"
    assert estimate.endpoints["calendar"] == "ok"


def test_travelpayouts_adapter_timeout_falls_back(monkeypatch):
    cache.clear()
    monkeypatch.setenv("TRAVELPAYOUTS_ENABLED", "true")
    monkeypatch.setenv("TRAVELPAYOUTS_API_TOKEN", "token")

    adapter = TravelpayoutsAdapter()
    with patch("httpx.request", side_effect=httpx.TimeoutException("timed out")):
        estimate = _estimate_payload(adapter)

    assert estimate.source == "fallback"
    assert estimate.error_type == "timeout"
    assert estimate.flight_min > 0
    assert estimate.hotel_nightly_min > 0


def test_travelpayouts_adapter_http_error_falls_back(monkeypatch):
    cache.clear()
    monkeypatch.setenv("TRAVELPAYOUTS_ENABLED", "true")
    monkeypatch.setenv("TRAVELPAYOUTS_API_TOKEN", "token")

    adapter = TravelpayoutsAdapter()

    def _http_503(*args, **kwargs):  # noqa: ANN002, ANN003
        return DummyResponse({}, status_code=503)

    with patch("httpx.request", side_effect=_http_503):
        estimate = _estimate_payload(adapter)

    assert estimate.source == "fallback"
    assert estimate.error_type in {"unknown", "rate_limit", "auth", "quota", "timeout"}
    assert any(status != "ok" for status in estimate.endpoints.values())

```

## planner/tests/test_scoring.py
```
from datetime import datetime, timezone

from planner.services.scoring import score_package


def test_score_package_components_shape():
    result = score_package(
        total_minor=180_000,
        budget_minor=220_000,
        preference_weights={"culture": 1.0, "food": 1.0},
        candidate_tags=["culture", "food", "nightlife"],
        season_multiplier=1.03,
        distance_band="medium",
        nonstop_likelihood=0.72,
        freshness_at=datetime(2026, 7, 1, 10, 0, tzinfo=timezone.utc),
    )

    assert result.score > 0
    assert "budget_fit" in result.breakdown
    assert "preference_match" in result.breakdown
    assert "seasonality" in result.breakdown
    assert "travel_time_proxy" in result.breakdown
    assert "freshness" in result.breakdown


def test_scoring_regression_deterministic_ranking():
    scenarios = [
        {
            "id": "balanced",
            "kwargs": {
                "total_minor": 160_000,
                "budget_minor": 220_000,
                "preference_weights": {"culture": 1.0, "food": 0.8},
                "candidate_tags": ["culture", "food", "quiet"],
                "season_multiplier": 1.0,
                "distance_band": "medium",
                "nonstop_likelihood": 0.78,
            },
        },
        {
            "id": "expensive_longhaul",
            "kwargs": {
                "total_minor": 320_000,
                "budget_minor": 220_000,
                "preference_weights": {"culture": 1.0, "food": 0.8},
                "candidate_tags": ["nature", "quiet"],
                "season_multiplier": 1.18,
                "distance_band": "ultra_long",
                "nonstop_likelihood": 0.22,
            },
        },
        {
            "id": "cheap_short",
            "kwargs": {
                "total_minor": 130_000,
                "budget_minor": 220_000,
                "preference_weights": {"culture": 1.0, "food": 0.8},
                "candidate_tags": ["culture", "food", "nightlife"],
                "season_multiplier": 0.96,
                "distance_band": "short",
                "nonstop_likelihood": 0.92,
            },
        },
    ]

    ranked = []
    freshness = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)
    for scenario in scenarios:
        scored = score_package(freshness_at=freshness, **scenario["kwargs"])
        ranked.append((scenario["id"], scored.score))

    ranked_ids = [item[0] for item in sorted(ranked, key=lambda item: item[1], reverse=True)]
    assert ranked_ids == ["balanced", "cheap_short", "expensive_longhaul"]

```

## planner/tests/test_package_builder.py
```
from datetime import timedelta
from decimal import Decimal

import pytest
from django.contrib.auth.models import User
from django.utils import timezone

from planner.models import DestinationCandidate, FlightOption, HotelOption, PlanRequest
from planner.services.package_builder import build_packages_for_plan


@pytest.mark.django_db
def test_build_packages_for_plan_creates_links_only_payload_fields():
    user = User.objects.create_user(username="alice", password="safe-pass")
    depart = timezone.now().date() + timedelta(days=30)
    ret = timezone.now().date() + timedelta(days=37)

    plan = PlanRequest.objects.create(
        user=user,
        origin_input="JFK",
        origin_code="JFK",
        destination_country="FR",
        date_mode=PlanRequest.DateMode.EXACT,
        depart_date=depart,
        return_date=ret,
        nights_min=4,
        nights_max=7,
        total_budget=Decimal("2400"),
        travelers=2,
        search_currency="USD",
        preference_weights={"culture": 1.0, "food": 1.0},
        status=PlanRequest.Status.BUILDING_PACKAGES,
    )
    paris = DestinationCandidate.objects.create(
        plan=plan,
        country_code="FR",
        city_name="Paris",
        airport_code="CDG",
        rank=1,
        metadata={"tier": "premium", "tags": ["culture", "food"], "nonstop_likelihood": 0.7},
    )

    FlightOption.objects.create(
        plan=plan,
        candidate=paris,
        provider="travelpayouts",
        external_offer_id="f1",
        origin_airport="JFK",
        destination_airport="CDG",
        airline_codes=["AF"],
        stops=0,
        duration_minutes=430,
        cabin_class="economy",
        currency="USD",
        total_price=Decimal("640"),
        deeplink_url="https://www.aviasales.com/search?origin=JFK&destination=CDG",
        raw_payload={
            "estimated_min": "580.00",
            "estimated_max": "760.00",
            "distance_band": "long",
            "nonstop_likelihood": 0.7,
            "season_multiplier": 1.05,
            "data_source": "travelpayouts",
        },
        last_checked_at=timezone.now(),
    )

    HotelOption.objects.create(
        plan=plan,
        candidate=paris,
        provider="travelpayouts",
        external_offer_id="h1",
        name="Paris partner hotels",
        star_rating=4.3,
        guest_rating=8.7,
        currency="USD",
        total_price=Decimal("980"),
        deeplink_url="https://www.booking.com/searchresults.html?ss=Paris%2C+FR",
        raw_payload={
            "nightly_min": "140.00",
            "nightly_max": "260.00",
            "distance_band": "long",
            "season_multiplier": 1.05,
            "data_source": "travelpayouts",
        },
        last_checked_at=timezone.now(),
    )

    packages = build_packages_for_plan(plan, sort_mode="best_value", max_packages=3)
    assert len(packages) == 1
    package = packages[0]

    assert package.rank == 1
    assert package.flight_url.startswith("https://")
    assert package.hotel_url.startswith("https://")
    assert package.tours_url.startswith("https://")
    assert package.estimated_total_min > 0
    assert package.estimated_total_max >= package.estimated_total_min
    assert package.score_breakdown.get("budget_fit") is not None
    assert package.freshness_at is not None

```

## planner/tests/test_click_and_invariants.py
```
from datetime import timedelta
from decimal import Decimal

import pytest
from django.contrib.auth.models import User
from django.urls import resolve
from django.utils import timezone
from rest_framework.test import APIClient, APIRequestFactory

from planner import api_urls
from planner.models import ClickEvent, DestinationCandidate, FlightOption, HotelOption, PackageOption, PlanRequest
from planner.serializers import PackageOptionSerializer


def _build_plan_with_package(user: User) -> tuple[PlanRequest, PackageOption]:
    depart = timezone.now().date() + timedelta(days=21)
    ret = timezone.now().date() + timedelta(days=27)
    plan = PlanRequest.objects.create(
        user=user,
        origin_input="JFK",
        origin_code="JFK",
        destination_country="FR",
        date_mode=PlanRequest.DateMode.EXACT,
        depart_date=depart,
        return_date=ret,
        nights_min=4,
        nights_max=6,
        total_budget=Decimal("2100"),
        travelers=2,
        search_currency="USD",
        status=PlanRequest.Status.COMPLETED,
    )
    candidate = DestinationCandidate.objects.create(
        plan=plan,
        country_code="FR",
        city_name="Paris",
        airport_code="CDG",
        rank=1,
        metadata={"tier": "premium", "tags": ["culture", "food"]},
    )
    flight = FlightOption.objects.create(
        plan=plan,
        candidate=candidate,
        provider="travelpayouts",
        external_offer_id="f-test",
        origin_airport="JFK",
        destination_airport="CDG",
        stops=0,
        duration_minutes=450,
        cabin_class="economy",
        currency="USD",
        total_price=Decimal("640"),
        deeplink_url="https://www.aviasales.com/search?origin=JFK&destination=CDG",
    )
    hotel = HotelOption.objects.create(
        plan=plan,
        candidate=candidate,
        provider="travelpayouts",
        external_offer_id="h-test",
        name="Paris hotels",
        star_rating=4.2,
        guest_rating=8.5,
        currency="USD",
        total_price=Decimal("920"),
        deeplink_url="https://www.booking.com/searchresults.html?ss=Paris",
    )
    package = PackageOption.objects.create(
        plan=plan,
        candidate=candidate,
        flight_option=flight,
        hotel_option=hotel,
        rank=1,
        currency="USD",
        total_price=Decimal("1660"),
        amount_minor=166000,
        estimated_total_min=Decimal("1450"),
        estimated_total_max=Decimal("1890"),
        estimated_flight_min=Decimal("590"),
        estimated_flight_max=Decimal("760"),
        estimated_hotel_nightly_min=Decimal("145"),
        estimated_hotel_nightly_max=Decimal("255"),
        flight_url="https://www.aviasales.com/search?origin=JFK&destination=CDG",
        hotel_url="https://www.booking.com/searchresults.html?ss=Paris",
        tours_url="https://www.getyourguide.com/s/?q=Paris",
        score=83.2,
        score_breakdown={"budget_fit": 80, "preference_match": 85, "seasonality": 78, "travel_time_proxy": 70, "freshness": 95},
    )
    return plan, package


@pytest.mark.django_db
def test_click_endpoint_stores_extended_click_fields():
    user = User.objects.create_user(username="tracker", password="safe-pass")
    plan, package = _build_plan_with_package(user)

    client = APIClient()
    client.force_authenticate(user=user)

    payload = {
        "provider": "travelpayouts",
        "link_type": "flight",
        "destination": "Paris-FR",
        "correlation_id": f"{plan.id}:{package.id}:flight",
        "plan_id": str(plan.id),
        "package_id": str(package.id),
        "outbound_url": "https://www.aviasales.com/search?origin=JFK&destination=CDG",
    }
    response = client.post("/api/click", data=payload, format="json")

    assert response.status_code == 201
    event = ClickEvent.objects.latest("created_at")
    assert event.plan_id == plan.id
    assert event.package_id == package.id
    assert event.link_type == "flight"
    assert event.destination == "Paris-FR"
    assert event.outbound_url.startswith("https://")


@pytest.mark.django_db
def test_links_only_invariant_for_api_and_package_payload():
    routes = [str(pattern.pattern) for pattern in api_urls.urlpatterns]
    assert all("checkout" not in route for route in routes)
    assert all("reservation" not in route for route in routes)

    user = User.objects.create_user(username="invariant", password="safe-pass")
    _, package = _build_plan_with_package(user)

    request = APIRequestFactory().get("/api/plans/test/packages")
    payload = PackageOptionSerializer(package, context={"request": request}).data

    assert payload["deeplinks"]["flight_url"].startswith("https://")
    assert payload["deeplinks"]["hotel_url"].startswith("https://")
    forbidden_keys = {"reservation_id", "booking_id", "checkout_url", "payment_url"}
    assert forbidden_keys.isdisjoint(payload.keys())

    match = resolve("/api/click")
    assert match.view_name == "planner-api:click-track"

```

## planner/migrations/0003_clickevent_correlation_id_clickevent_destination_and_more.py
```
# Generated by Django 5.2.11 on 2026-02-19 18:03

import django.utils.timezone
from decimal import Decimal
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('planner', '0002_flightoption_amount_minor_and_more'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='clickevent',
            name='correlation_id',
            field=models.CharField(blank=True, db_index=True, max_length=64),
        ),
        migrations.AddField(
            model_name='clickevent',
            name='destination',
            field=models.CharField(blank=True, max_length=128),
        ),
        migrations.AddField(
            model_name='clickevent',
            name='link_type',
            field=models.CharField(choices=[('flight', 'Flight'), ('hotel', 'Hotel'), ('tour', 'Tour'), ('other', 'Other')], db_index=True, default='other', max_length=16),
        ),
        migrations.AddField(
            model_name='clickevent',
            name='outbound_url',
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name='destinationcandidate',
            name='metadata',
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name='packageoption',
            name='estimated_flight_max',
            field=models.DecimalField(decimal_places=2, default=Decimal('0'), max_digits=10),
        ),
        migrations.AddField(
            model_name='packageoption',
            name='estimated_flight_min',
            field=models.DecimalField(decimal_places=2, default=Decimal('0'), max_digits=10),
        ),
        migrations.AddField(
            model_name='packageoption',
            name='estimated_hotel_nightly_max',
            field=models.DecimalField(decimal_places=2, default=Decimal('0'), max_digits=10),
        ),
        migrations.AddField(
            model_name='packageoption',
            name='estimated_hotel_nightly_min',
            field=models.DecimalField(decimal_places=2, default=Decimal('0'), max_digits=10),
        ),
        migrations.AddField(
            model_name='packageoption',
            name='estimated_total_max',
            field=models.DecimalField(decimal_places=2, default=Decimal('0'), max_digits=10),
        ),
        migrations.AddField(
            model_name='packageoption',
            name='estimated_total_min',
            field=models.DecimalField(decimal_places=2, default=Decimal('0'), max_digits=10),
        ),
        migrations.AddField(
            model_name='packageoption',
            name='flight_url',
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name='packageoption',
            name='freshness_at',
            field=models.DateTimeField(db_index=True, default=django.utils.timezone.now),
        ),
        migrations.AddField(
            model_name='packageoption',
            name='hotel_url',
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name='packageoption',
            name='tours_url',
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name='planrequest',
            name='preference_weights',
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddIndex(
            model_name='clickevent',
            index=models.Index(fields=['link_type', 'clicked_at'], name='planner_cli_link_ty_92aef0_idx'),
        ),
    ]

```


