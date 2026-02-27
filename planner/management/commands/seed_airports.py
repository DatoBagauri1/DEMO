from __future__ import annotations

import csv
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from planner.models import Airport
from planner.services.airports import refresh_airports_top_cache, set_airports_dataset_metadata


DEFAULT_AIRPORT_DATASET = Path(__file__).resolve().parents[2] / "data" / "airports.csv"


def _country_code(country: str) -> str:
    if not country:
        return ""
    probe = country.strip().upper()
    if len(probe) == 2 and probe.isalpha():
        return probe
    return ""


class Command(BaseCommand):
    help = "Seed Airport rows from bundled CSV dataset."

    def add_arguments(self, parser) -> None:  # noqa: ANN001
        parser.add_argument(
            "--path",
            default=str(DEFAULT_AIRPORT_DATASET),
            help="Path to airports CSV dataset.",
        )
        parser.add_argument(
            "--replace",
            action="store_true",
            help="Delete existing airports before seeding.",
        )

    @transaction.atomic
    def handle(self, *args, **options):  # noqa: ANN002, ANN003, ANN201
        dataset_path = Path(options["path"]).resolve()
        if not dataset_path.exists():
            raise CommandError(f"Dataset not found: {dataset_path}")

        if options["replace"]:
            Airport.objects.all().delete()

        with dataset_path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            required = {"iata", "name", "city", "country", "lat", "lon", "timezone"}
            missing = required.difference(reader.fieldnames or [])
            if missing:
                raise CommandError(f"Dataset missing required columns: {', '.join(sorted(missing))}")

            rows = []
            for raw in reader:
                code = (raw.get("iata") or "").strip().upper()
                if len(code) != 3 or not code.isalpha():
                    continue
                try:
                    lat = float(raw.get("lat") or 0)
                    lon = float(raw.get("lon") or 0)
                except ValueError:
                    lat = None
                    lon = None
                rows.append(
                    Airport(
                        iata=code,
                        name=(raw.get("name") or "").strip()[:255],
                        city=(raw.get("city") or "").strip()[:128] or code,
                        country=(raw.get("country") or "").strip()[:128] or "Unknown",
                        country_code=((raw.get("country_code") or "").strip().upper()[:2] or _country_code(raw.get("country") or "")),
                        latitude=lat,
                        longitude=lon,
                        timezone=(raw.get("timezone") or "").strip()[:64],
                        search_blob=" ".join(
                            [
                                code,
                                (raw.get("name") or "").strip(),
                                (raw.get("city") or "").strip(),
                                (raw.get("country") or "").strip(),
                            ],
                        ).lower()[:512],
                    ),
                )

        existing = {item.iata: item.id for item in Airport.objects.only("id", "iata")}
        to_create: list[Airport] = []
        to_update: list[Airport] = []
        for row in rows:
            if row.iata in existing:
                row.id = existing[row.iata]
                to_update.append(row)
            else:
                to_create.append(row)

        if to_create:
            Airport.objects.bulk_create(to_create, batch_size=1000)
        if to_update:
            Airport.objects.bulk_update(
                to_update,
                fields=["name", "city", "country", "country_code", "latitude", "longitude", "timezone", "search_blob"],
                batch_size=1000,
            )

        loaded_count = Airport.objects.count()
        loaded_at = timezone.now()
        set_airports_dataset_metadata(loaded_count=loaded_count, loaded_at=loaded_at)
        refresh_airports_top_cache()
        self.stdout.write(self.style.SUCCESS(f"Seeded airports: {loaded_count} records"))
