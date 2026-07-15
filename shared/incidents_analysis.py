"""Incident CSV validation and analytics utilities."""

from __future__ import annotations

import csv
import io
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable


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

EXPECTED_CONTEXT_METRICS = {
    "total_records": 100,
    "valid_records": 95,
    "invalid_records": 5,
    "invalid_breakdown": {
        "invalid_tracking": 1,
        "carrier_country_mismatch": 1,
        "invalid_category": 1,
        "invalid_email": 1,
        "closed_without_score": 1,
    },
    "category_breakdown": {
        "LOST_PARCEL": 14,
        "DELAYED_DELIVERY": 38,
        "WRONG_ADDRESS": 19,
        "RETURN_REQUEST": 17,
        "DAMAGE": 7,
    },
    "status_breakdown": {"OPEN": 29, "CLOSED": 52, "DISCARDED": 14},
    "country_breakdown": {"US": 50, "ES": 45},
    "satisfaction": {
        "scored_incidents": 52,
        "average_score": 3.06,
        "score_distribution": {1: 6, 2: 11, 3: 15, 4: 14, 5: 6},
    },
}


@dataclass(slots=True)
class AnalysisResult:
    source_file: str
    total_records: int
    valid_records: int
    invalid_records: int
    invalid_breakdown: dict[str, int]
    category_breakdown: dict[str, int]
    status_breakdown: dict[str, int]
    country_breakdown: dict[str, int]
    scored_closed_incidents: int
    closed_incidents: int
    average_satisfaction: float | None
    satisfaction_distribution: dict[int, int]

    def to_summary_json(self) -> dict:
        return {
            "source_file": self.source_file,
            "totals": {
                "total": self.total_records,
                "valid": self.valid_records,
                "invalid": self.invalid_records,
            },
            "invalid_breakdown": self.invalid_breakdown,
            "breakdown": {
                "category": self.category_breakdown,
                "status": self.status_breakdown,
                "country": self.country_breakdown,
            },
            "satisfaction": {
                "closed_incidents": self.closed_incidents,
                "scored_incidents": self.scored_closed_incidents,
                "average": self.average_satisfaction,
                "distribution": self.satisfaction_distribution,
            },
        }


def _to_percent(part: int, whole: int) -> str:
    if whole == 0:
        return "0.0%"
    return f"{(part / whole) * 100:.1f}%"


def _parse_csv_rows(csv_text: str) -> list[dict[str, str]]:
    reader = csv.DictReader(io.StringIO(csv_text))
    if reader.fieldnames is None:
        raise ValueError("CSV without header row")

    missing_cols = REQUIRED_COLUMNS - set(reader.fieldnames)
    if missing_cols:
        cols = ", ".join(sorted(missing_cols))
        raise ValueError(f"CSV missing required columns: {cols}")

    return list(reader)


def _validate_row(row: dict[str, str]) -> tuple[set[str], int | None]:
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


def analyze_incidents_from_text(csv_text: str, source_file: str = "uploaded.csv") -> AnalysisResult:
    rows = _parse_csv_rows(csv_text)
    invalid_counter: Counter[str] = Counter()
    valid_rows: list[tuple[dict[str, str], int | None]] = []

    for row in rows:
        violations, score = _validate_row(row)
        if violations:
            invalid_counter.update(violations)
        else:
            valid_rows.append((row, score))

    valid_count = len(valid_rows)
    total_count = len(rows)

    category_counter = Counter(row["category"].strip() for row, _ in valid_rows)
    status_counter = Counter(row["status"].strip() for row, _ in valid_rows)
    country_counter = Counter(row["country"].strip() for row, _ in valid_rows)

    closed_scores = [
        score
        for row, score in valid_rows
        if row["status"].strip() == "CLOSED" and score is not None
    ]
    closed_incidents = status_counter.get("CLOSED", 0)

    average_satisfaction = None
    if closed_scores:
        average_satisfaction = round(sum(closed_scores) / len(closed_scores), 2)

    score_distribution = {score: closed_scores.count(score) for score in range(1, 6)}

    invalid_breakdown = {
        rule: invalid_counter.get(rule, 0) for rule in INVALID_RULE_LABELS.keys()
    }

    return AnalysisResult(
        source_file=source_file,
        total_records=total_count,
        valid_records=valid_count,
        invalid_records=total_count - valid_count,
        invalid_breakdown=invalid_breakdown,
        category_breakdown={
            category: category_counter.get(category, 0)
            for category in sorted(VALID_CATEGORIES)
        },
        status_breakdown={status: status_counter.get(status, 0) for status in ["OPEN", "CLOSED", "DISCARDED"]},
        country_breakdown={country: country_counter.get(country, 0) for country in ["US", "ES"]},
        scored_closed_incidents=len(closed_scores),
        closed_incidents=closed_incidents,
        average_satisfaction=average_satisfaction,
        satisfaction_distribution=score_distribution,
    )


def analyze_incidents_file(csv_path: str | Path) -> AnalysisResult:
    path = Path(csv_path)
    if not path.exists():
        raise FileNotFoundError(f"CSV file not found: {path}")

    with path.open("r", encoding="utf-8", newline="") as file:
        csv_text = file.read()

    if not csv_text.strip():
        raise ValueError("CSV file is empty")

    return analyze_incidents_from_text(csv_text, source_file=path.name)


def compare_against_expected_context(result: AnalysisResult) -> dict[str, object]:
    checks = {
        "total_records": result.total_records == EXPECTED_CONTEXT_METRICS["total_records"],
        "valid_records": result.valid_records == EXPECTED_CONTEXT_METRICS["valid_records"],
        "invalid_records": result.invalid_records == EXPECTED_CONTEXT_METRICS["invalid_records"],
        "invalid_breakdown": {
            rule: result.invalid_breakdown.get(rule, 0)
            == EXPECTED_CONTEXT_METRICS["invalid_breakdown"].get(rule, 0)
            for rule in EXPECTED_CONTEXT_METRICS["invalid_breakdown"].keys()
        },
        "category_breakdown": result.category_breakdown
        == EXPECTED_CONTEXT_METRICS["category_breakdown"],
        "status_breakdown": result.status_breakdown
        == EXPECTED_CONTEXT_METRICS["status_breakdown"],
        "country_breakdown": result.country_breakdown
        == EXPECTED_CONTEXT_METRICS["country_breakdown"],
        "scored_incidents": result.scored_closed_incidents
        == EXPECTED_CONTEXT_METRICS["satisfaction"]["scored_incidents"],
        "average_score": result.average_satisfaction
        == EXPECTED_CONTEXT_METRICS["satisfaction"]["average_score"],
        "score_distribution": result.satisfaction_distribution
        == EXPECTED_CONTEXT_METRICS["satisfaction"]["score_distribution"],
    }
    invalid_rule_checks = checks["invalid_breakdown"]
    checks["invalid_breakdown"] = bool(invalid_rule_checks) and all(invalid_rule_checks.values())
    checks["all_match"] = all(
        value if isinstance(value, bool) else bool(value)
        for key, value in checks.items()
        if key != "all_match"
    )
    checks["invalid_breakdown_details"] = invalid_rule_checks
    return checks


def build_console_report(result: AnalysisResult) -> str:
    valid = result.valid_records
    lines: list[str] = []
    lines.append("=" * 60)
    lines.append("  TRACKFLOW - INCIDENT REPORT ANALYSIS")
    lines.append(f"  Source file: {result.source_file}")
    lines.append("=" * 60)
    lines.append("")
    lines.append(f"TOTAL RECORDS IN FILE ......... {result.total_records}")
    lines.append(f"  |- Valid records .............. {result.valid_records}")
    lines.append(f"  '- Invalid / incomplete ....... {result.invalid_records}")
    lines.append("")
    lines.append("INVALID RECORDS BREAKDOWN")
    lines.append(
        f"  |- Invalid tracking number .... {result.invalid_breakdown['invalid_tracking']}"
    )
    lines.append(
        "  |- Carrier/country mismatch ... "
        f"{result.invalid_breakdown['carrier_country_mismatch']}"
    )
    lines.append(
        "  |- Invalid or missing category  "
        f"{result.invalid_breakdown['invalid_category']}"
    )
    lines.append(
        f"  |- Invalid or missing email ... {result.invalid_breakdown['invalid_email']}"
    )
    lines.append(
        f"  '- Closed incident, no score .. {result.invalid_breakdown['closed_without_score']}"
    )
    lines.append("")
    lines.append("BREAKDOWN BY CATEGORY (valid records)")
    for category in [
        "LOST_PARCEL",
        "DELAYED_DELIVERY",
        "WRONG_ADDRESS",
        "RETURN_REQUEST",
        "DAMAGE",
    ]:
        count = result.category_breakdown.get(category, 0)
        lines.append(
            f"  - {category:<20} {count:>3} ({_to_percent(count, valid):>6})"
        )

    lines.append("")
    lines.append("BREAKDOWN BY STATUS (valid records)")
    for status in ["OPEN", "CLOSED", "DISCARDED"]:
        count = result.status_breakdown.get(status, 0)
        lines.append(f"  - {status:<20} {count:>3} ({_to_percent(count, valid):>6})")

    lines.append("")
    lines.append("BREAKDOWN BY COUNTRY (valid records)")
    for country in ["US", "ES"]:
        count = result.country_breakdown.get(country, 0)
        lines.append(f"  - {country:<20} {count:>3} ({_to_percent(count, valid):>6})")

    lines.append("")
    lines.append("SATISFACTION INDEX (closed incidents)")
    lines.append(
        f"  Scored incidents: {result.scored_closed_incidents} of {result.closed_incidents}"
    )
    if result.average_satisfaction is None:
        lines.append("  Average score: N/A")
    else:
        lines.append(f"  Average score: {result.average_satisfaction:.2f} / 5.00")

    score_labels = {
        1: "Very dissatisfied",
        2: "Dissatisfied",
        3: "Neutral",
        4: "Satisfied",
        5: "Very satisfied",
    }
    for score in range(1, 6):
        lines.append(
            "  - Score "
            f"{score} ({score_labels[score]:<17}) {result.satisfaction_distribution.get(score, 0)}"
        )

    lines.append("")
    verification = compare_against_expected_context(result)
    lines.append("CONTEXT VERIFICATION")
    lines.append(
        "  - Expected values match: "
        + ("YES" if verification["all_match"] else "NO")
    )
    lines.append("=" * 60)
    return "\n".join(lines)


def build_metrics_rows(result: AnalysisResult) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    rows.append({"metric": "total_records", "group": "totals", "name": "total", "value": str(result.total_records)})
    rows.append({"metric": "valid_records", "group": "totals", "name": "valid", "value": str(result.valid_records)})
    rows.append({"metric": "invalid_records", "group": "totals", "name": "invalid", "value": str(result.invalid_records)})

    for key, label in INVALID_RULE_LABELS.items():
        rows.append(
            {
                "metric": "invalid_breakdown",
                "group": "invalid_rules",
                "name": label,
                "value": str(result.invalid_breakdown.get(key, 0)),
            }
        )

    for category, value in result.category_breakdown.items():
        rows.append(
            {
                "metric": "category_breakdown",
                "group": "category",
                "name": category,
                "value": str(value),
            }
        )

    for status, value in result.status_breakdown.items():
        rows.append(
            {
                "metric": "status_breakdown",
                "group": "status",
                "name": status,
                "value": str(value),
            }
        )

    for country, value in result.country_breakdown.items():
        rows.append(
            {
                "metric": "country_breakdown",
                "group": "country",
                "name": country,
                "value": str(value),
            }
        )

    rows.append(
        {
            "metric": "satisfaction_index",
            "group": "satisfaction",
            "name": "average_score",
            "value": "" if result.average_satisfaction is None else f"{result.average_satisfaction:.2f}",
        }
    )
    rows.append(
        {
            "metric": "satisfaction_index",
            "group": "satisfaction",
            "name": "closed_incidents",
            "value": str(result.closed_incidents),
        }
    )
    rows.append(
        {
            "metric": "satisfaction_index",
            "group": "satisfaction",
            "name": "scored_incidents",
            "value": str(result.scored_closed_incidents),
        }
    )
    for score in range(1, 6):
        rows.append(
            {
                "metric": "satisfaction_distribution",
                "group": "satisfaction",
                "name": f"score_{score}",
                "value": str(result.satisfaction_distribution.get(score, 0)),
            }
        )

    return rows


def metrics_to_csv_text(result: AnalysisResult) -> str:
    output = io.StringIO()
    fieldnames = ["metric", "group", "name", "value"]
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(build_metrics_rows(result))
    return output.getvalue()


def write_metrics_csv(result: AnalysisResult, output_path: str | Path) -> Path:
    path = Path(output_path)
    csv_text = metrics_to_csv_text(result)
    with path.open("w", encoding="utf-8", newline="") as file:
        file.write(csv_text)
    return path
