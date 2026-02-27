from __future__ import annotations

from datetime import timedelta
import math

from django.utils import timezone

from planner.models import ProviderCall, ProviderError
from planner.services.airports import airports_dataset_metadata
from planner.services.places import places_last_success_at
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
    payload["airports_dataset"] = {
        **airports_dataset_metadata(),
        "enabled": True,
    }
    places_metrics = _provider_metrics("places", flags.get("places_enabled", True))
    places_success = places_last_success_at()
    if places_success:
        places_metrics["last_success_at"] = places_success
    places_metrics["source"] = "wikimedia+fallback"
    payload["places"] = {
        **places_metrics,
    }

    # Keep legacy keys for backward compatibility while links-only mode is default.
    payload["duffel"] = _provider_metrics("duffel", flags.get("duffel_enabled", False))
    payload["amadeus"] = _provider_metrics("amadeus", flags.get("amadeus_enabled", False))
    payload["expedia_rapid"] = _provider_metrics("expedia_rapid", flags.get("expedia_enabled", False))
    return payload
