from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass
class PackageScore:
    score: float
    price_score: float
    convenience_score: float
    quality_score: float
    location_score: float
    explanations: list[str]
    breakdown: dict


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _component_price_value(total_minor: int, budget_minor: int) -> tuple[float, str]:
    if budget_minor <= 0:
        return 55.0, "Budget not provided, using neutral price-value score."
    ratio = total_minor / max(1, budget_minor)
    score = _clamp(100 - abs(ratio - 0.85) * 120, 0, 100)
    if ratio <= 0.9:
        note = "Estimated total is within budget comfort zone."
    elif ratio <= 1.05:
        note = "Estimated total is close to your budget target."
    else:
        note = "Estimated total exceeds your target budget."
    return round(score, 2), note


def _component_preference_match(preference_weights: dict[str, float], candidate_tags: list[str]) -> tuple[float, str]:
    if not preference_weights:
        return 62.0, "No explicit preferences supplied, using balanced defaults."

    tags = {tag.lower().strip() for tag in candidate_tags}
    total_weight = 0.0
    matched_weight = 0.0
    for key, raw_weight in preference_weights.items():
        label = str(key).lower().strip()
        try:
            weight = float(raw_weight)
        except (TypeError, ValueError):
            weight = 0.0
        if weight <= 0:
            continue
        total_weight += weight
        if label in tags:
            matched_weight += weight

    if total_weight <= 0:
        return 58.0, "Preference weights normalized to zero; fallback match score applied."

    score = _clamp((matched_weight / total_weight) * 100.0, 0, 100)
    if score >= 80:
        note = "Strong match with your preference profile."
    elif score >= 55:
        note = "Partial match with your preference profile."
    else:
        note = "Weak preference match for this destination."
    return round(score, 2), note


def _component_seasonal_fit(season_multiplier: float) -> tuple[float, str]:
    score = _clamp(96 - abs(season_multiplier - 1.0) * 140, 20, 100)
    if season_multiplier >= 1.14:
        note = "Peak-season pressure may raise prices and crowding."
    elif season_multiplier <= 0.92:
        note = "Off-season timing likely improves value."
    else:
        note = "Seasonality is in a moderate band."
    return round(score, 2), note


def _component_convenience(
    distance_band: str,
    nonstop_likelihood: float,
    timezone_delta_hours: float,
    travel_time_minutes: int,
) -> tuple[float, str]:
    distance_scores = {
        "short": 92.0,
        "medium": 78.0,
        "long": 60.0,
        "ultra_long": 45.0,
    }
    distance_score = distance_scores.get(distance_band, 68.0)
    nonstop_score = _clamp(nonstop_likelihood, 0.0, 1.0) * 100

    timezone_penalty = _clamp(timezone_delta_hours * 4.5, 0, 38)
    redeye_proxy = 0.0
    if travel_time_minutes >= 420:
        redeye_proxy = _clamp((travel_time_minutes - 420) / 18.0, 0, 22)

    raw = (distance_score * 0.45) + (nonstop_score * 0.35) + (100 - timezone_penalty) * 0.2
    score = _clamp(raw - redeye_proxy, 0, 100)

    if score >= 80:
        note = "Convenient route proxy: favorable duration, timezone shift, and nonstop odds."
    elif score >= 60:
        note = "Moderate convenience for route timing and duration."
    else:
        note = "Long-haul or timezone-heavy route may reduce comfort."
    return round(score, 2), note


def _component_safety_fallback(data_confidence: float) -> tuple[float, str]:
    confidence = _clamp(data_confidence, 0.0, 1.0)
    score = round(45 + (confidence * 55), 2)
    if confidence >= 0.8:
        note = "High confidence data mix with strong provider coverage."
    elif confidence >= 0.55:
        note = "Mixed live/fallback data confidence."
    else:
        note = "Low confidence: fallback-heavy estimates were used."
    return score, note


def _component_freshness(freshness_at: datetime | None) -> tuple[float, str]:
    if not freshness_at:
        return 42.0, "Freshness timestamp unavailable; conservative freshness score applied."

    if freshness_at.tzinfo is None:
        freshness_at = freshness_at.replace(tzinfo=timezone.utc)
    now = datetime.now(tz=timezone.utc)
    age_hours = max(0.0, (now - freshness_at).total_seconds() / 3600)

    if age_hours <= 4:
        return 100.0, "Price snapshot is very recent."
    if age_hours <= 24:
        return 86.0, "Price snapshot is within the last day."
    if age_hours <= 72:
        return 68.0, "Price snapshot is a few days old."
    if age_hours <= 168:
        return 52.0, "Price snapshot is about a week old."
    return 34.0, "Price snapshot is stale and may drift."


def score_package(
    *,
    total_minor: int,
    budget_minor: int,
    preference_weights: dict[str, float],
    candidate_tags: list[str],
    season_multiplier: float,
    distance_band: str,
    nonstop_likelihood: float,
    freshness_at: datetime | None,
    timezone_delta_hours: float = 0.0,
    travel_time_minutes: int = 0,
    data_confidence: float = 0.75,
) -> PackageScore:
    price_value, price_note = _component_price_value(total_minor, budget_minor)
    convenience, convenience_note = _component_convenience(
        distance_band=distance_band,
        nonstop_likelihood=nonstop_likelihood,
        timezone_delta_hours=timezone_delta_hours,
        travel_time_minutes=travel_time_minutes,
    )
    preference_match, preference_note = _component_preference_match(preference_weights, candidate_tags)
    seasonal_fit, seasonal_note = _component_seasonal_fit(season_multiplier)
    safety_fallback, safety_note = _component_safety_fallback(data_confidence)
    freshness, freshness_note = _component_freshness(freshness_at)

    score = (
        (price_value * 0.28)
        + (convenience * 0.20)
        + (preference_match * 0.17)
        + (seasonal_fit * 0.13)
        + (safety_fallback * 0.12)
        + (freshness * 0.10)
    )

    explanations = [price_note, convenience_note, preference_note, seasonal_note, safety_note, freshness_note]
    breakdown = {
        "price_value": round(price_value, 2),
        "convenience": round(convenience, 2),
        "preference_match": round(preference_match, 2),
        "seasonal_fit": round(seasonal_fit, 2),
        "safety_fallback": round(safety_fallback, 2),
        "freshness": round(freshness, 2),
        "weights": {
            "price_value": 0.28,
            "convenience": 0.20,
            "preference_match": 0.17,
            "seasonal_fit": 0.13,
            "safety_fallback": 0.12,
            "freshness": 0.10,
        },
        "explanations": explanations,
    }

    return PackageScore(
        score=round(score, 2),
        price_score=round(price_value, 2),
        convenience_score=round(convenience, 2),
        quality_score=round(preference_match, 2),
        location_score=round(seasonal_fit, 2),
        explanations=explanations,
        breakdown=breakdown,
    )
