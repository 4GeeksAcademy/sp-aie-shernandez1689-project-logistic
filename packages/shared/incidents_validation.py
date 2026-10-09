"""Shared validation rules for TrackFlow historical incident CSV files."""

from __future__ import annotations

import csv
import io
from datetime import datetime


REQUIRED_COLUMNS = {
    "incident_id",
    "date",
    "country",
    "customer_type",
    "tracking_number",
    "carrier",
    "category",
    "description",
    "status",
    "customer_email",
    "satisfaction_score",
}

VALID_COUNTRIES = {"US", "ES"}
VALID_CUSTOMER_TYPES = {"B2B", "B2C"}
VALID_CATEGORIES = {
    "LOST_PARCEL",
    "DELAYED_DELIVERY",
    "WRONG_ADDRESS",
    "RETURN_REQUEST",
    "DAMAGE",
}
VALID_STATUSES = {"OPEN", "CLOSED", "DISCARDED"}
VALID_CARRIERS_BY_COUNTRY = {
    "US": {"UPS", "FEDEX", "DHL_US"},
    "ES": {"MRW", "SEUR", "DHL_ES", "LOCAL_ES"},
}

INVALID_RULE_LABELS = {
    "invalid_country": "Country missing or invalid",
    "invalid_customer_type": "Customer type missing or invalid",
    "invalid_tracking": "Invalid tracking number",
    "carrier_country_mismatch": "Carrier/country mismatch",
    "invalid_category": "Invalid or missing category",
    "invalid_description": "Invalid or missing description",
    "invalid_email": "Invalid or missing email",
    "invalid_status": "Status missing or invalid",
    "invalid_date": "Date missing or invalid",
    "score_not_integer": "Satisfaction score is not an integer",
    "score_out_of_range": "Satisfaction score out of range",
    "closed_without_score": "Closed incident, no score",
}


def parse_incident_rows(
    csv_text: str,
    *,
    allow_missing_incident_id: bool = False,
) -> list[dict[str, str]]:
    reader = csv.DictReader(io.StringIO(csv_text))
    if reader.fieldnames is None:
        raise ValueError("CSV without header row")

    required_columns = REQUIRED_COLUMNS
    if allow_missing_incident_id:
        required_columns = REQUIRED_COLUMNS - {"incident_id"}

    missing_cols = required_columns - set(reader.fieldnames)
    if missing_cols:
        cols = ", ".join(sorted(missing_cols))
        raise ValueError(f"CSV missing required columns: {cols}")

    return list(reader)


def validate_incident_row(row: dict[str, str]) -> tuple[set[str], int | None]:
    violations: set[str] = set()

    country = (row.get("country") or "").strip()
    customer_type = (row.get("customer_type") or "").strip()
    tracking_number = (row.get("tracking_number") or "").strip()
    carrier = (row.get("carrier") or "").strip()
    category = (row.get("category") or "").strip()
    description = (row.get("description") or "").strip()
    status = (row.get("status") or "").strip()
    customer_email = (row.get("customer_email") or "").strip()
    score_raw = (row.get("satisfaction_score") or "").strip()
    date_raw = (row.get("date") or "").strip()

    if country not in VALID_COUNTRIES:
        violations.add("invalid_country")

    if customer_type not in VALID_CUSTOMER_TYPES:
        violations.add("invalid_customer_type")

    if not tracking_number or len(tracking_number) < 8:
        violations.add("invalid_tracking")

    if not carrier or carrier not in VALID_CARRIERS_BY_COUNTRY.get(country, set()):
        violations.add("carrier_country_mismatch")

    if category not in VALID_CATEGORIES:
        violations.add("invalid_category")

    if not description or len(description) < 5:
        violations.add("invalid_description")

    if not customer_email or "@" not in customer_email:
        violations.add("invalid_email")

    if status not in VALID_STATUSES:
        violations.add("invalid_status")

    try:
        datetime.strptime(date_raw, "%Y-%m-%d")
    except ValueError:
        violations.add("invalid_date")

    score: int | None = None
    if score_raw:
        try:
            score = int(score_raw)
        except ValueError:
            violations.add("score_not_integer")
        else:
            if score < 1 or score > 5:
                violations.add("score_out_of_range")

    if status == "CLOSED" and not score_raw:
        violations.add("closed_without_score")

    return violations, score