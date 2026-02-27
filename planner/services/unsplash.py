import logging
import os
import random
from pathlib import Path
from urllib.parse import quote, quote_plus

import httpx
from django.core.cache import cache
from planner.services.http_client import build_http_client

logger = logging.getLogger(__name__)

_DESTINATION_DIR = Path(__file__).resolve().parents[1] / "static" / "img" / "destinations"


def _discover_local_image_pool() -> list[str]:
    allowed_exts = {".jpg", ".jpeg", ".png", ".webp", ".svg", ".avif", ".gif"}
    if not _DESTINATION_DIR.exists():
        return ["/static/img/destinations/travel-adventure-japan-night-landscape.jpg"]

    files = sorted(
        path for path in _DESTINATION_DIR.iterdir()
        if path.is_file() and path.suffix.lower() in allowed_exts
    )
    if not files:
        return ["/static/img/destinations/travel-adventure-japan-night-landscape.jpg"]
    return [f"/static/img/destinations/{quote(path.name)}" for path in files]


LOCAL_IMAGE_POOL = _discover_local_image_pool()


def get_destination_image(query: str) -> str:
    key = os.getenv("UNSPLASH_ACCESS_KEY")
    if not key:
        return random.choice(LOCAL_IMAGE_POOL)

    cache_key = f"unsplash:{quote_plus(query.lower())}"
    cached = cache.get(cache_key)
    if cached:
        return cached

    url = "https://api.unsplash.com/photos/random"
    params = {"query": f"{query} travel city", "orientation": "landscape", "client_id": key}
    try:
        with build_http_client(accept="application/json") as client:
            response = client.get(url, params=params)
            response.raise_for_status()
            image_url = response.json().get("urls", {}).get("regular")
        if image_url:
            cache.set(cache_key, image_url, timeout=60 * 60 * 12)
            return image_url
    except httpx.HTTPError as exc:
        logger.warning("Unsplash request failed: %s", exc)
    return random.choice(LOCAL_IMAGE_POOL)


def get_rotating_hero_images() -> list[str]:
    destinations = ["Paris", "Tokyo", "Bangkok", "Barcelona", "Vancouver"]
    return [get_destination_image(item) for item in destinations]
