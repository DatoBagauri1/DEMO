from datetime import datetime, timezone

from planner.services.scoring import score_package


def test_score_package_components_shape():
    result = score_package(
        total_minor=180_000,
        budget_minor=220_000,
        preference_weights={"culture": 1.0, "food": 1.0},
        candidate_tags=["culture", "food", "nightlife"],
        season_multiplier=1.03,
        distance_band="medium",
        nonstop_likelihood=0.72,
        freshness_at=datetime(2026, 7, 1, 10, 0, tzinfo=timezone.utc),
        timezone_delta_hours=2,
        travel_time_minutes=360,
        data_confidence=0.85,
    )

    assert result.score > 0
    assert "price_value" in result.breakdown
    assert "convenience" in result.breakdown
    assert "preference_match" in result.breakdown
    assert "seasonal_fit" in result.breakdown
    assert "safety_fallback" in result.breakdown
    assert "freshness" in result.breakdown


def test_scoring_regression_expensive_longhaul_is_last():
    scenarios = [
        {
            "id": "balanced",
            "kwargs": {
                "total_minor": 160_000,
                "budget_minor": 220_000,
                "preference_weights": {"culture": 1.0, "food": 0.8},
                "candidate_tags": ["culture", "food", "quiet"],
                "season_multiplier": 1.0,
                "distance_band": "medium",
                "nonstop_likelihood": 0.78,
                "timezone_delta_hours": 2,
                "travel_time_minutes": 410,
                "data_confidence": 0.9,
            },
        },
        {
            "id": "expensive_longhaul",
            "kwargs": {
                "total_minor": 320_000,
                "budget_minor": 220_000,
                "preference_weights": {"culture": 1.0, "food": 0.8},
                "candidate_tags": ["nature", "quiet"],
                "season_multiplier": 1.18,
                "distance_band": "ultra_long",
                "nonstop_likelihood": 0.22,
                "timezone_delta_hours": 8,
                "travel_time_minutes": 860,
                "data_confidence": 0.45,
            },
        },
        {
            "id": "cheap_short",
            "kwargs": {
                "total_minor": 130_000,
                "budget_minor": 220_000,
                "preference_weights": {"culture": 1.0, "food": 0.8},
                "candidate_tags": ["culture", "food", "nightlife"],
                "season_multiplier": 0.96,
                "distance_band": "short",
                "nonstop_likelihood": 0.92,
                "timezone_delta_hours": 1,
                "travel_time_minutes": 190,
                "data_confidence": 0.88,
            },
        },
    ]

    ranked = []
    freshness = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)
    for scenario in scenarios:
        scored = score_package(freshness_at=freshness, **scenario["kwargs"])
        ranked.append((scenario["id"], scored.score))

    ranked_ids = [item[0] for item in sorted(ranked, key=lambda item: item[1], reverse=True)]
    assert ranked_ids[-1] == "expensive_longhaul"
