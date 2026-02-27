from django.urls import path

from planner import api_views

app_name = "planner-api"

urlpatterns = [
    path("airports/search", api_views.AirportSearchAPIView.as_view(), name="airports-search"),
    path("profile/me", api_views.ProfileMeAPIView.as_view(), name="profile-me"),
    path("places/saved", api_views.SavedPlacesListAPIView.as_view(), name="places-saved"),
    path("places/save-toggle", api_views.SavedPlaceSaveToggleAPIView.as_view(), name="place-save-toggle"),
    path("providers/status", api_views.ProviderStatusAPIView.as_view(), name="provider-status"),
    path("providers/health", api_views.ProviderHealthAPIView.as_view(), name="provider-health"),
    path("plans/interpret", api_views.PlanInterpretAPIView.as_view(), name="plan-interpret"),
    path("plans/start", api_views.PlanStartAPIView.as_view(), name="plan-start"),
    path("plans/<uuid:plan_id>/refresh", api_views.PlanRefreshAPIView.as_view(), name="plan-refresh"),
    path("plans/<uuid:plan_id>/status", api_views.PlanStatusAPIView.as_view(), name="plan-status"),
    path("plans/<uuid:plan_id>/packages", api_views.PlanPackagesAPIView.as_view(), name="plan-packages"),
    path("packages/<uuid:package_id>/save-toggle", api_views.PackageSaveToggleAPIView.as_view(), name="package-save-toggle"),
    path("click", api_views.ClickTrackingAPIView.as_view(), name="click-track"),
]
