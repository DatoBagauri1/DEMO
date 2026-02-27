from django.contrib import admin

from planner.models import (
    Airport,
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


@admin.register(Airport)
class AirportAdmin(admin.ModelAdmin):
    list_display = ("iata", "name", "city", "country", "country_code", "timezone")
    list_filter = ("country_code",)
    search_fields = ("iata", "name", "city", "country")


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
        "destination_iata",
        "search_mode",
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
