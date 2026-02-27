from django.urls import include, path

from planner import views

urlpatterns = [
    path("signup/", views.signup_view, name="signup"),
    path("profile/", views.profile_view, name="profile"),
    path("", include("django.contrib.auth.urls")),
]

