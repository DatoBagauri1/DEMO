from __future__ import annotations

from datetime import date
from urllib.parse import parse_qsl, quote_plus, urlencode, urlparse, urlunparse

from planner.services.config import travelpayouts_marker


def _merge_query(url: str, extra_params: dict[str, str]) -> str:
    parsed = urlparse(url)
    existing = dict(parse_qsl(parsed.query, keep_blank_values=True))
    existing.update({k: v for k, v in extra_params.items() if v not in (None, "")})
    query = urlencode(existing, doseq=True)
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, query, parsed.fragment))


def _tracking_params(
    *,
    provider: str,
    plan_id: str | None,
    package_id: str | None,
    link_type: str | None,
    destination: str | None,
) -> dict[str, str]:
    params = {
        "utm_source": "trippilot",
        "utm_medium": "affiliate",
        "utm_campaign": "country_planner",
        "tp_provider": provider,
    }
    marker = travelpayouts_marker()
    if marker:
        params["affiliate_id"] = marker
        params["marker"] = marker
    if plan_id:
        params["tp_plan"] = str(plan_id)
    if package_id:
        params["tp_package"] = str(package_id)
    if link_type:
        params["tp_link_type"] = link_type
    if destination:
        params["tp_destination"] = destination
    return params


def build_tracked_deeplink(
    url: str,
    *,
    provider: str,
    plan_id: str | None = None,
    package_id: str | None = None,
    link_type: str | None = None,
    destination: str | None = None,
) -> str:
    if not url:
        return url
    return _merge_query(
        url,
        _tracking_params(
            provider=provider,
            plan_id=plan_id,
            package_id=package_id,
            link_type=link_type,
            destination=destination,
        ),
    )


def resolve_partner_deeplink(
    *,
    item_url: str | None,
    search_url: str,
    provider: str,
    plan_id: str | None = None,
    package_id: str | None = None,
    link_type: str | None = None,
    destination: str | None = None,
) -> tuple[str, str, bool]:
    item_candidate = str(item_url or "").strip()
    if item_candidate:
        return (
            build_tracked_deeplink(
                item_candidate,
                provider=provider,
                plan_id=plan_id,
                package_id=package_id,
                link_type=link_type,
                destination=destination,
            ),
            "item",
            False,
        )
    return (
        build_tracked_deeplink(
            search_url,
            provider=provider,
            plan_id=plan_id,
            package_id=package_id,
            link_type=link_type,
            destination=destination,
        ),
        "search",
        True,
    )


def build_flight_search_link(
    *,
    origin: str,
    destination: str,
    depart_date: date,
    return_date: date | None,
    travelers: int,
    cabin: str | None = None,
    plan_id: str | None = None,
    package_id: str | None = None,
    destination_label: str | None = None,
) -> str:
    base_url = "https://www.aviasales.com/search"
    params = {
        "origin": origin,
        "destination": destination,
        "depart_date": depart_date.isoformat(),
        "adults": max(1, int(travelers)),
    }
    if return_date:
        params["return_date"] = return_date.isoformat()
    raw_url = _merge_query(base_url, params)
    return build_tracked_deeplink(
        raw_url,
        provider="travelpayouts",
        plan_id=plan_id,
        package_id=package_id,
        link_type="flight",
        destination=destination_label,
    )


def build_hotel_search_link(
    *,
    city: str,
    country_code: str,
    checkin: date,
    checkout: date,
    adults: int,
    plan_id: str | None = None,
    package_id: str | None = None,
) -> str:
    base_url = "https://www.booking.com/searchresults.html"
    marker = travelpayouts_marker()
    params = {
        "ss": f"{city}, {country_code}",
        "checkin": checkin.isoformat(),
        "checkout": checkout.isoformat(),
        "group_adults": max(1, int(adults)),
        "no_rooms": 1,
    }
    if marker:
        params["aid"] = marker
    raw_url = _merge_query(base_url, params)
    return build_tracked_deeplink(
        raw_url,
        provider="travelpayouts",
        plan_id=plan_id,
        package_id=package_id,
        link_type="hotel",
        destination=f"{city}-{country_code}",
    )


def build_tour_search_link(
    *,
    city: str,
    country_code: str,
    plan_id: str | None = None,
    package_id: str | None = None,
) -> str:
    query = quote_plus(f"{city} {country_code}")
    raw_url = f"https://www.getyourguide.com/s/?q={query}"
    return build_tracked_deeplink(
        raw_url,
        provider="travelpayouts",
        plan_id=plan_id,
        package_id=package_id,
        link_type="tour",
        destination=f"{city}-{country_code}",
    )


def affiliate_configured() -> bool:
    return bool(travelpayouts_marker())
