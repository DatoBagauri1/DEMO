from __future__ import annotations

from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse


class MarketingPagesTests(TestCase):
    def test_why_page_returns_200_and_contains_key_headings(self):
        response = self.client.get(reverse("planner:why"))

        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn("Why Choose Our Service", html)
        self.assertIn("How it works", html)
        self.assertIn("Links-only", html)

    @patch("planner.views.get_rotating_hero_images", return_value=["img/destinations/travel-adventure-japan-night-landscape.jpg"])
    def test_navbar_contains_link_to_why(self, _mocked_hero_images):
        response = self.client.get(reverse("planner:landing"))

        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn('href="/why/"', html)
        self.assertIn("Our Service", html)

    @patch("planner.views.get_rotating_hero_images", return_value=["img/destinations/travel-adventure-japan-night-landscape.jpg"])
    def test_landing_page_contains_vibecoding_footer_line(self, _mocked_hero_images):
        response = self.client.get(reverse("planner:landing"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Made by Baga with VIBECODING")
