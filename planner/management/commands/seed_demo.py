from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.utils import timezone

from planner.models import DestinationCandidate, FlightOption, HotelOption, PlanRequest
from planner.services.deeplinks import build_tracked_deeplink
from planner.services.fx import to_minor_units
from planner.services.package_builder import build_packages_for_plan

User = get_user_model()


class Command(BaseCommand):
    help = "Seed demo user and demo plans for local development."

    def handle(self, *args, **options):  # noqa: ANN002, ANN003, ANN201
        user, created = User.objects.get_or_create(
            username="demo",
            defaults={"email": "demo@trippilot.local"},
        )
        if created:
            user.set_password("DemoPass123!")
            user.save(update_fields=["password"])

        now = timezone.now().date()
        plan_specs = [
            {
                "country": "FR",
                "origin": "JFK",
                "city": "Paris",
                "airport": "CDG",
                "lat": 48.8566,
                "lng": 2.3522,
                "flight_price": Decimal("520.00"),
                "hotel_price": Decimal("930.00"),
            },
            {
                "country": "JP",
                "origin": "LAX",
                "city": "Tokyo",
                "airport": "HND",
                "lat": 35.6762,
                "lng": 139.6503,
                "flight_price": Decimal("780.00"),
                "hotel_price": Decimal("860.00"),
            },
        ]

        for index, spec in enumerate(plan_specs, start=1):
            plan = PlanRequest.objects.create(
                user=user,
                origin_input=spec["origin"],
                origin_code=spec["origin"],
                origin_iata=spec["origin"],
                destination_iata=spec["airport"],
                destination_iatas=[spec["airport"]],
                destination_country=spec["country"],
                date_mode=PlanRequest.DateMode.EXACT,
                depart_date=now + timedelta(days=20 + (index * 4)),
                return_date=now + timedelta(days=27 + (index * 4)),
                departure_date_from=now + timedelta(days=20 + (index * 4)),
                departure_date_to=now + timedelta(days=20 + (index * 4)),
                trip_length_min=5,
                trip_length_max=8,
                nights_min=5,
                nights_max=8,
                total_budget=Decimal("2500.00"),
                travelers=2,
                adults=2,
                children=0,
                search_currency="USD",
                status=PlanRequest.Status.COMPLETED,
                progress_message="Demo seed complete",
                progress_percent=100,
                started_at=timezone.now(),
                completed_at=timezone.now(),
            )
            candidate = DestinationCandidate.objects.create(
                plan=plan,
                country_code=spec["country"],
                city_name=spec["city"],
                airport_code=spec["airport"],
                latitude=spec["lat"],
                longitude=spec["lng"],
                rank=1,
            )
            flight = FlightOption.objects.create(
                plan=plan,
                candidate=candidate,
                provider="demo_flights",
                external_offer_id=f"demo-flight-{plan.id}",
                origin_airport=spec["origin"],
                destination_airport=spec["airport"],
                departure_at=timezone.now() + timedelta(days=20),
                return_at=timezone.now() + timedelta(days=27),
                airline_codes=["TP"],
                stops=0,
                duration_minutes=460,
                cabin_class="economy",
                currency="USD",
                total_price=spec["flight_price"],
                amount_minor=to_minor_units(spec["flight_price"]),
                deeplink_url=build_tracked_deeplink(
                    "https://example.com/flight-demo",
                    provider="demo_flights",
                    plan_id=str(plan.id),
                ),
                last_checked_at=timezone.now(),
                raw_payload={"demo": True},
            )
            HotelOption.objects.create(
                plan=plan,
                candidate=candidate,
                provider="demo_hotels",
                external_offer_id=f"demo-hotel-{plan.id}",
                name=f"{spec['city']} Central Hotel",
                star_rating=4.2,
                guest_rating=8.6,
                neighborhood="City Center",
                latitude=spec["lat"],
                longitude=spec["lng"],
                distance_km=1.2,
                amenities=["wifi", "gym", "breakfast"],
                currency="USD",
                total_price=spec["hotel_price"],
                amount_minor=to_minor_units(spec["hotel_price"]),
                deeplink_url=build_tracked_deeplink(
                    "https://example.com/hotel-demo",
                    provider="demo_hotels",
                    plan_id=str(plan.id),
                ),
                last_checked_at=timezone.now(),
                raw_payload={"demo": True, "review_count": 340},
            )
            build_packages_for_plan(plan, sort_mode="best_value", max_packages=6)
            self.stdout.write(self.style.SUCCESS(f"Seeded demo plan {plan.id}"))

        self.stdout.write(self.style.SUCCESS("Demo seed complete. Login with demo / DemoPass123!"))
