"""Incidents analysis API for TrackFlow backoffice."""

from __future__ import annotations

import sys
from pathlib import Path

from flask import Flask, jsonify, make_response, request, send_from_directory


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from shared.incidents_analysis import (  # noqa: E402
    analyze_incidents_from_text,
    compare_against_expected_context,
    metrics_to_csv_text,
)


app = Flask(__name__)
UIS_ROOT = REPO_ROOT / "uis"
BACKOFFICE_ROOT = UIS_ROOT / "backoffice"

LAST_ANALYSIS_JSON: dict | None = None
LAST_ANALYSIS_CSV: str | None = None


def _error_response(message: str, status_code: int):
    payload = {"error": message}
    return jsonify(payload), status_code


def _extract_uploaded_csv() -> tuple[str | None, str | None, tuple | None]:
    if "file" not in request.files:
        return None, None, _error_response(
            "Missing file in request. Use multipart/form-data with field 'file'.",
            400,
        )

    uploaded_file = request.files["file"]
    if not uploaded_file.filename:
        return None, None, _error_response("Uploaded file has no name.", 400)

    content = uploaded_file.read()
    if not content.strip():
        return None, None, _error_response("Uploaded file is empty.", 400)

    try:
        csv_text = content.decode("utf-8")
    except UnicodeDecodeError:
        return None, None, _error_response(
            "Invalid file encoding. CSV must be UTF-8.", 400
        )

    return uploaded_file.filename, csv_text, None


@app.get("/")
def homepage():
    return send_from_directory(UIS_ROOT, "index.html")


@app.get("/application.html")
def application_page():
    return send_from_directory(UIS_ROOT, "application.html")


@app.get("/styles.css")
def site_styles():
    return send_from_directory(UIS_ROOT, "styles.css")


@app.get("/validation.js")
def site_validation_script():
    return send_from_directory(UIS_ROOT, "validation.js")


@app.get("/logo-logistica.svg")
def site_logo():
    return send_from_directory(UIS_ROOT, "logo-logistica.svg")


@app.get("/backoffice/")
def backoffice_home():
    return send_from_directory(BACKOFFICE_ROOT, "index.html")


@app.get("/backoffice/<path:filename>")
def backoffice_assets(filename: str):
    return send_from_directory(BACKOFFICE_ROOT, filename)


@app.post("/api/inbcidents/analyze")
@app.post("/api/incidents/analyze")
def analyze_incidents_endpoint():
    file_name, csv_text, error = _extract_uploaded_csv()
    if error is not None:
        return error

    try:
        result = analyze_incidents_from_text(csv_text, source_file=file_name)
    except ValueError as exc:
        return _error_response(f"Invalid CSV format: {exc}", 400)

    global LAST_ANALYSIS_JSON, LAST_ANALYSIS_CSV
    summary = result.to_summary_json()
    summary["context_verification"] = compare_against_expected_context(result)
    LAST_ANALYSIS_JSON = summary
    LAST_ANALYSIS_CSV = metrics_to_csv_text(result)

    return jsonify(summary), 200


@app.get("/api/incidents/results/export")
def export_last_result():
    if LAST_ANALYSIS_CSV is None:
        return _error_response(
            "No analysis available yet. Run POST /api/inbcidents/analyze first.",
            404,
        )

    response = make_response(LAST_ANALYSIS_CSV)
    response.headers["Content-Type"] = "text/csv; charset=utf-8"
    response.headers["Content-Disposition"] = "attachment; filename=resultados.csv"
    return response


@app.get("/api/incidents/results/latest")
def latest_result():
    if LAST_ANALYSIS_JSON is None:
        return _error_response("No analysis available yet.", 404)
    return jsonify(LAST_ANALYSIS_JSON), 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=True)
