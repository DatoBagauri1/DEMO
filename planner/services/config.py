import os


def env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def links_only_enabled() -> bool:
    return env_bool("TRIPPILOT_LINKS_ONLY", default=True)


def travelpayouts_enabled() -> bool:
    return env_bool("TRAVELPAYOUTS_ENABLED", default=True)


def travelpayouts_base_currency() -> str:
    return os.getenv("TRAVELPAYOUTS_BASE_CURRENCY", "USD").upper().strip() or "USD"


def travelpayouts_marker() -> str:
    return (
        os.getenv("TRAVELPAYOUTS_MARKER", "").strip()
        or os.getenv("TRIPPILOT_AFFILIATE_ID", "").strip()
    )


def default_origin_iata() -> str:
    return os.getenv("DEFAULT_ORIGIN_IATA", "").strip().upper()


def travelpayouts_api_token() -> str:
    return os.getenv("TRAVELPAYOUTS_API_TOKEN", "").strip()
