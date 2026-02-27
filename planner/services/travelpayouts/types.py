from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any


@dataclass
class CandidateEstimate:
    provider: str
    source: str
    currency: str
    flight_min: Decimal
    flight_max: Decimal
    hotel_nightly_min: Decimal
    hotel_nightly_max: Decimal
    freshness_at: datetime
    distance_km: float
    distance_band: str
    travel_time_minutes: int
    nonstop_likelihood: float
    season_multiplier: float
    tier: str
    tags: list[str]
    raw_payload: dict[str, Any] = field(default_factory=dict)
    endpoints: dict[str, str] = field(default_factory=dict)
    error_type: str | None = None
    http_status: int | None = None
    error_summary: str = ""
    latency_ms: int | None = None

    @property
    def flight_mid(self) -> Decimal:
        return ((self.flight_min + self.flight_max) / Decimal("2")).quantize(Decimal("0.01"))

    @property
    def hotel_nightly_mid(self) -> Decimal:
        return ((self.hotel_nightly_min + self.hotel_nightly_max) / Decimal("2")).quantize(Decimal("0.01"))
