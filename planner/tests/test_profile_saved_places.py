from __future__ import annotations

from html import unescape

import pytest
from django.contrib.auth.models import User
from django.urls import reverse
from rest_framework.test import APIClient

from planner.models import SavedPlace


def _auth_client(user: User) -> APIClient:
    client = APIClient()
    client.force_authenticate(user=user)
    return client


@pytest.mark.django_db
def test_profile_page_renders_personal_info_and_saved_places_list(client):
    user = User.objects.create_user(
        username="profile_reader",
        password="safe-pass",
        email="profile@example.com",
        first_name="Profile",
        last_name="Reader",
    )
    SavedPlace.objects.create(
        user=user,
        name="Eiffel Tower",
        city="Paris",
        country="FR",
        source="wikipedia",
        external_id="wiki:eiffel_tower",
        image_url="https://upload.wikimedia.org/eiffel.jpg",
        outbound_url="https://en.wikipedia.org/wiki/Eiffel_Tower",
    )

    client.force_login(user)
    response = client.get(reverse("profile"), HTTP_HOST="localhost")

    assert response.status_code == 200
    html = unescape(response.content.decode())
    assert "Profile Defaults" not in html
    assert "Places I Want to See" in html
    assert "profile_reader" in html
    assert "profile@example.com" in html
    assert "Profile" in html
    assert "Reader" in html
    assert "Eiffel Tower" in html
    assert "Paris" in html


@pytest.mark.django_db
def test_save_toggle_creates_savedplace_and_it_appears_in_profile(client, monkeypatch):
    monkeypatch.delenv("OUTBOUND_URL_ALLOWED_DOMAINS", raising=False)
    user = User.objects.create_user(username="place_saver", password="safe-pass", email="save@example.com")
    api = _auth_client(user)

    payload = {
        "name": "Louvre Museum",
        "city": "Paris",
        "country": "FR",
        "source": "wikipedia",
        "external_id": "wiki:louvre_museum",
        "image_url": "https://upload.wikimedia.org/louvre.jpg",
        "outbound_url": "https://en.wikipedia.org/wiki/Louvre",
    }
    toggle_response = api.post("/api/places/save-toggle", data=payload, format="json")

    assert toggle_response.status_code == 201
    assert toggle_response.json()["saved"] is True
    assert SavedPlace.objects.filter(user=user, external_id="wiki:louvre_museum").exists()

    profile_me = api.get("/api/profile/me")
    assert profile_me.status_code == 200
    assert profile_me.json()["saved_places_count"] == 1

    saved_list = api.get("/api/places/saved")
    assert saved_list.status_code == 200
    saved_payload = saved_list.json()
    assert saved_payload["count"] == 1
    assert saved_payload["results"][0]["name"] == "Louvre Museum"

    client.force_login(user)
    profile_response = client.get(reverse("profile"), HTTP_HOST="localhost")
    html = unescape(profile_response.content.decode())
    assert "Louvre Museum" in html
    assert "Open" in html
    assert "Remove" in html


@pytest.mark.django_db
def test_save_toggle_is_user_isolated(client):
    user_a = User.objects.create_user(username="user_a_places", password="safe-pass")
    user_b = User.objects.create_user(username="user_b_places", password="safe-pass")

    saved = SavedPlace.objects.create(
        user=user_a,
        name="Colosseum",
        city="Rome",
        country="IT",
        source="manual",
        external_id="manual:colosseum",
        outbound_url="https://en.wikipedia.org/wiki/Colosseum",
    )

    api_b = _auth_client(user_b)
    list_response = api_b.get("/api/places/saved")
    assert list_response.status_code == 200
    assert list_response.json()["count"] == 0

    forbidden_toggle = api_b.post("/api/places/save-toggle", data={"saved_place_id": saved.id}, format="json")
    assert forbidden_toggle.status_code == 404

    client.force_login(user_b)
    profile_response = client.get(reverse("profile"), HTTP_HOST="localhost")
    html = unescape(profile_response.content.decode())
    assert "Colosseum" not in html
    assert "No saved places yet" in html


@pytest.mark.django_db
@pytest.mark.parametrize("unsafe_url", ["javascript:alert(1)", "file:///C:/temp/test.txt"])
def test_outbound_url_validation_rejects_unsafe_urls(unsafe_url: str, monkeypatch):
    monkeypatch.delenv("OUTBOUND_URL_ALLOWED_DOMAINS", raising=False)
    user = User.objects.create_user(username=f"unsafe_{abs(hash(unsafe_url))}", password="safe-pass")
    api = _auth_client(user)

    response = api.post(
        "/api/places/save-toggle",
        data={
            "name": "Unsafe Place",
            "city": "Nowhere",
            "country": "XX",
            "source": "manual",
            "outbound_url": unsafe_url,
        },
        format="json",
    )

    assert response.status_code == 400
    payload = response.json()
    assert payload["detail"] == "validation_error"
    assert SavedPlace.objects.filter(user=user).count() == 0
