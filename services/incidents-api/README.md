# Incidents API

Backend service that exposes incident analysis endpoints used by the backoffice UI.

## Endpoints

- `POST /api/inbcidents/analyze` (also available as `POST /api/incidents/analyze`)
  - Request: `multipart/form-data` with `file` field containing CSV.
  - Response: JSON summary with totals, invalid breakdown, category/status/country breakdown, satisfaction metrics, and context verification.
- `GET /api/incidents/results/export`
  - Response: downloadable CSV with one row per metric.
- `GET /api/incidents/results/latest`
  - Response: last JSON analysis payload.

## Run locally

```bash
cd services/incidents-api
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

The API starts on `http://localhost:8000`.

## Quick start (API + UI)

Run one command from the repository root:

```bash
python services/incidents-api/app.py
```

Then open:

- `http://localhost:8000/` (main website)
- `http://localhost:8000/backoffice/` (incidents analysis screen)
