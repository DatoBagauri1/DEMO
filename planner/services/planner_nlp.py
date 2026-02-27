from __future__ import annotations

import re
from datetime import date, timedelta


MONTHS = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}


def _next_month_anchor(today: date) -> date:
    month = today.month + 1
    year = today.year
    if month > 12:
        month = 1
        year += 1
    return date(year, month, 1)


def parse_trip_text(text: str) -> dict:
    raw = (text or "").strip()
    if not raw:
        return {"fields": {}, "warnings": ["No planner text provided."]}

    lowered = raw.lower()
    fields: dict = {}
    warnings: list[str] = []

    from_match = re.search(r"\bfrom\s+([a-z]{3})\b", lowered)
    to_match = re.search(r"\bto\s+([a-z]{3})\b", lowered)
    direct_iatas = re.findall(r"\b([a-z]{3})\b", lowered)
    if from_match:
        fields["origin_iata"] = from_match.group(1).upper()
    elif direct_iatas:
        fields["origin_iata"] = direct_iatas[0].upper()

    if to_match:
        fields["destination_iata"] = to_match.group(1).upper()
        fields["search_mode"] = "direct"
    elif any(keyword in lowered for keyword in ["explore", "anywhere", "surprise me", "open to destinations"]):
        fields["search_mode"] = "explore"
    elif len(direct_iatas) >= 2:
        fields["destination_iata"] = direct_iatas[1].upper()
        fields["search_mode"] = "direct"

    adults_match = re.search(r"\b(\d+)\s+adult", lowered)
    children_match = re.search(r"\b(\d+)\s+child", lowered)
    travelers_match = re.search(r"\b(\d+)\s+traveler", lowered)
    if adults_match:
        fields["adults"] = int(adults_match.group(1))
    elif travelers_match:
        fields["adults"] = int(travelers_match.group(1))
    if children_match:
        fields["children"] = int(children_match.group(1))

    if re.search(r"(?:budget|under|around)\s*\$?\s*([0-9]+(?:[.,][0-9]{1,2})?)", lowered):
        warnings.append("Budget preferences are ignored in concrete-offer mode.")

    nights_match = re.search(r"\bfor\s+(\d+)\s+(?:night|day)", lowered)
    range_match = re.search(r"\b(\d+)\s*-\s*(\d+)\s*(?:night|day)", lowered)
    if range_match:
        fields["trip_length_min"] = int(range_match.group(1))
        fields["trip_length_max"] = int(range_match.group(2))
    elif nights_match:
        nights = int(nights_match.group(1))
        fields["trip_length_min"] = nights
        fields["trip_length_max"] = nights

    today = date.today()
    if "next month" in lowered:
        month_anchor = _next_month_anchor(today)
        fields["travel_month"] = month_anchor.isoformat()
    else:
        for label, month_num in MONTHS.items():
            if label in lowered:
                year = today.year + 1 if month_num < today.month else today.year
                fields["travel_month"] = date(year, month_num, 1).isoformat()
                break

    if "weekend" in lowered and "trip_length_min" not in fields:
        fields["trip_length_min"] = 2
        fields["trip_length_max"] = 3

    if any(phrase in lowered for phrase in ("business class", "first class", "premium economy", "economy class")):
        warnings.append("Cabin class preferences are ignored in links-only mode.")

    preferences = {}
    for key in ["beach", "nature", "culture", "nightlife", "food", "quiet", "family", "luxury", "adventure"]:
        if key in lowered:
            preferences[key] = 1.0
    if preferences:
        fields["preferences"] = preferences

    if "search_mode" not in fields:
        if "destination_iata" in fields:
            fields["search_mode"] = "direct"
        else:
            fields["search_mode"] = "explore"
            warnings.append("No destination airport detected, switched to explore mode.")

    if "origin_iata" not in fields:
        warnings.append("No origin airport detected. Include text like 'from JFK'.")

    if "travel_month" not in fields and "departure_date_from" not in fields and "depart_date" not in fields:
        # Set a safe default planning window if user only supplied free-text constraints.
        default_start = today + timedelta(days=30)
        fields["departure_date_from"] = default_start.isoformat()
        fields["departure_date_to"] = (default_start + timedelta(days=7)).isoformat()

    return {"fields": fields, "warnings": warnings}
