from django.urls import path

from planner import views

app_name = "planner"

urlpatterns = [
    path("", views.landing, name="landing"),
    path("why/", views.why_view, name="why"),
    path("planner/", views.wizard_view, name="wizard"),
    path("plans/<uuid:plan_id>/", views.results_view, name="results"),
    path("plans/<uuid:plan_id>/progress/", views.progress_partial, name="progress-partial"),
    path("plans/<uuid:plan_id>/packages/", views.package_cards_partial, name="packages-partial"),
    path("packages/<uuid:package_id>/toggle-save/", views.toggle_save_package, name="toggle-save"),
    path("p/<str:token>/pkg/<uuid:package_id>/", views.package_detail_public_view, name="package-detail-public"),
    path("share/<str:token>/", views.public_share_view, name="share"),
]
