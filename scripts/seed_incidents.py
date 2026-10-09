"""Load validated historical CSV incidents into the TrackFlow TinyDB."""

from __future__ import annotations

import argparse
import hashlib
import sys
from datetime import datetime, timezone
from pathlib import Path

from pydantic import ValidationError
from tinydb import TinyDB


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from services.api.models import Incident  # noqa: E402
from shared.incidents_analysis import (  # noqa: E402
    parse_incident_rows,
    validate_incident_row,
)


DEFAULT_CSV_PATH = (
    REPO_ROOT
    / "content"
    / "contexts"
    / "incidents-file-analysis"
    / "incidents-trackflow.csv"
)
DEFAULT_DB_PATH = REPO_ROOT / "services" / "api" / "data" / "tinydb.json"
INCIDENTS_TABLE = "incidents"
SEED_KEYS_TABLE = "incident_seed_keys"

STATUS_MAP = {
    "OPEN": "open",
    "CLOSED": "resolved",
    "DISCARDED": "discarded",
}
CATEGORY_MAP = {
    "LOST_PARCEL": "lost_parcel",
    "DELAYED_DELIVERY": "carrier_issue",
    "WRONG_ADDRESS": "delivery_failure",
    "RETURN_REQUEST": "returns_issue",
    "DAMAGE": "carrier_issue",
}
BRANCH_MAP = {
    "US": "la_office",
    "ES": "zaragoza_office",
}


def _source_key(row: dict[str, str], title: str, created_at: datetime) -> str:
    incident_id = (row.get("incident_id") or "").strip()
    if incident_id:
        source_identifier = f"incident_id:{incident_id}"
    else:
        source_identifier = f"title_date:{title}\x1f{created_at.isoformat()}"
    return hashlib.sha256(source_identifier.encode("utf-8")).hexdigest()


def _invalid_message(error: ValidationError) -> str:
    return "; ".join(
        f"{'.'.join(str(part) for part in item['loc'])}: {item['msg']}"
        for item in error.errors(include_url=False)
    )


def seed_incidents(
    csv_path: Path = DEFAULT_CSV_PATH,
    db_path: Path = DEFAULT_DB_PATH,
) -> tuple[int, int, list[str]]:
    csv_text = csv_path.read_text(encoding="utf-8-sig")
    rows = parse_incident_rows(csv_text, allow_missing_incident_id=True)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    db = TinyDB(db_path)
    incidents_table = db.table(INCIDENTS_TABLE)
    seed_keys_table = db.table(SEED_KEYS_TABLE)
    existing_keys = {record["source_key"] for record in seed_keys_table.all()}
    inserted_count = 0
    skipped_count = 0
    invalid_rows: list[str] = []

    try:
        for row_number, row in enumerate(rows, start=2):
            violations, _ = validate_incident_row(row)
            if violations:
                invalid_rows.append(
                    f"Fila {row_number}: {', '.join(sorted(violations))}"
                )
                continue

            description = row["description"]
            title = description.strip()[:120]
            created_at = datetime.strptime(row["date"].strip(), "%Y-%m-%d").replace(
                tzinfo=timezone.utc
            )
            source_key = _source_key(row, title, created_at)
            if source_key in existing_keys:
                skipped_count += 1
                continue

            try:
                incident = Incident(
                    title=title,
                    description=description,
                    category=CATEGORY_MAP[row["category"].strip()],
                    status=STATUS_MAP[row["status"].strip()],
                    origin="customer",
                    branch=BRANCH_MAP[row["country"].strip()],
                    created_at=created_at,
                    updated_at=created_at,
                )
            except (KeyError, ValidationError, ValueError) as error:
                if isinstance(error, ValidationError):
                    message = _invalid_message(error)
                else:
                    message = str(error)
                invalid_rows.append(f"Fila {row_number}: {message}")
                continue

            incidents_table.insert(incident.model_dump(mode="json"))
            seed_keys_table.insert({"source_key": source_key})
            existing_keys.add(source_key)
            inserted_count += 1
    finally:
        db.close()

    return inserted_count, skipped_count, invalid_rows


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Seed validated historical incidents from a CSV file."
    )
    parser.add_argument(
        "csv_path",
        nargs="?",
        type=Path,
        default=DEFAULT_CSV_PATH,
        help=f"CSV source (default: {DEFAULT_CSV_PATH})",
    )
    parser.add_argument(
        "--database",
        type=Path,
        default=DEFAULT_DB_PATH,
        help=f"TinyDB file (default: {DEFAULT_DB_PATH})",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        inserted, skipped, invalid_rows = seed_incidents(args.csv_path, args.database)
    except (OSError, ValueError) as error:
        print(f"Error al cargar incidencias: {error}")
        return 1

    print(
        f"Seed finalizado: {inserted} insertadas, {skipped} duplicadas omitidas, "
        f"{len(invalid_rows)} inválidas."
    )
    if invalid_rows:
        print("Filas inválidas:")
        for message in invalid_rows:
            print(f"- {message}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())