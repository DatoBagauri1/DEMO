from __future__ import annotations

import os

from planner.services.config import links_only_enabled, travelpayouts_enabled
from planner.services.fx import fx_configured
from planner.services.travelpayouts.adapter import TravelpayoutsAdapter


def get_market_provider() -> TravelpayoutsAdapter:
    return TravelpayoutsAdapter()


def provider_status() -> dict[str, bool]:
    links_only = links_only_enabled()
    return {
        "links_only_enabled": links_only,
        "travelpayouts_enabled": travelpayouts_enabled(),
        "travelpayouts_token_configured": bool(os.getenv("TRAVELPAYOUTS_API_TOKEN")),
        "travelpayouts_marker_configured": bool(os.getenv("TRAVELPAYOUTS_MARKER") or os.getenv("TRIPPILOT_AFFILIATE_ID")),
        "airports_enabled": True,
        "places_enabled": True,
        # Keep legacy keys for API compatibility, disabled in links-only production mode.
        "duffel_enabled": False if links_only else bool(os.getenv("DUFFEL_ACCESS_TOKEN")),
        "amadeus_enabled": False if links_only else bool(os.getenv("AMADEUS_CLIENT_ID") and os.getenv("AMADEUS_CLIENT_SECRET")),
        "expedia_enabled": False if links_only else bool(os.getenv("EXPEDIA_RAPID_KEY")),
        "fx_enabled": fx_configured(),
    }
