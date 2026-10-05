"""CLI script to analyze TrackFlow incidents CSV."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from shared.incidents_analysis import (  # noqa: E402
    analyze_incidents_file,
    compare_against_expected_context,
    build_console_report,
    write_metrics_csv,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Analyze incidents CSV and print summary metrics."
    )
    parser.add_argument("csv_path", help="Path to the incidents CSV file")
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    try:
        result = analyze_incidents_file(args.csv_path)
    except FileNotFoundError as error:
        print(f"Error: {error}")
        return 1
    except ValueError as error:
        print(f"Error: {error}")
        return 1

    print(build_console_report(result))

    checks = compare_against_expected_context(result)
    if not checks["all_match"]:
        print(
            "Warning: The output does not fully match CONTEXT.incidents.md expected values."
        )

    user_choice = input("Deseas exportar los resultados a CSV? (s/n): ").strip().lower()
    if user_choice == "s":
        output_path = write_metrics_csv(result, "resultados.csv")
        print(f"Archivo exportado: {output_path}")
    else:
        print("Exportacion omitida.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())