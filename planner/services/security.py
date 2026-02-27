from __future__ import annotations

import os
from urllib.parse import urlparse


def allowed_outbound_domains() -> set[str]:
    raw = os.getenv("OUTBOUND_URL_ALLOWED_DOMAINS", "")
    return {item.strip().lower() for item in raw.split(",") if item.strip()}


def is_allowed_outbound_url(url: str) -> bool:
    if not url:
        return False
    try:
        parsed = urlparse(url)
    except Exception:  # noqa: BLE001
        return False

    if parsed.scheme not in {"http", "https"}:
        return False
    if not parsed.netloc:
        return False

    hostname = (parsed.hostname or "").lower()
    domains = allowed_outbound_domains()
    if not domains:
        return True
    return any(hostname == domain or hostname.endswith(f".{domain}") for domain in domains)
