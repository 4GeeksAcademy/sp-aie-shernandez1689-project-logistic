# Incidents API

Backend service that exposes incident management and CSV analysis endpoints.

## Incident management API

The FastAPI app (`uvicorn main:app` from `services/api`) exposes these
authenticated endpoints. Creating incidents and changing their status require
the `admin` or `manager` role.

- `POST /api/incidents`: create an incident; invalid or missing fields return
  HTTP 400 with a descriptive validation message.
- `GET /api/incidents`: list incidents; optional filters are `status`,
  `origin`, `branch`, and `category`.
- `GET /api/incidents/summary`: totals by status, category, origin, and branch.
- `GET /api/incidents/{id}`: retrieve one incident; returns HTTP 404 if absent.
- `PATCH /api/incidents/{id}/status`: update status only and enforce the
  lifecycle transitions defined in `CONTEXT-inc.md`.

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
cd services/api
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

The API starts on `http://localhost:8000`.

## Quick start (API + UI)

Run one command from the repository root:

```bash
python services/api/app.py
```

Then open:

- `http://localhost:8000/` (main website)
- `http://localhost:8000/web/` (incidents analysis screen)
