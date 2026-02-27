from django.contrib import admin
from django.urls import include, path

from planner import views as planner_views

urlpatterns = [
    path("admin/", admin.site.urls),
    path("healthz/", planner_views.healthz, name="healthz"),
    path("accounts/", include("planner.auth_urls")),
    path("api/", include("planner.api_urls")),
    path("", include("planner.urls")),
]
