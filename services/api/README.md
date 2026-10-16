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

## Telemetry

### `POST /telemetry/events` — Ingesta (endpoint real)

- Body: `{"events": [...]}` — each event follows the standard envelope
  defined in `docs/telemetry/telemetry-plan.md` (eventId, timestamp,
  sessionId, userId, event_type, schemaVersion, requestId, properties).
- Behavior: validates each event individually with `TelemetryEvent`,
  **rejects invalid events without cancelling the batch**, and persists the
  valid ones in Supabase (`telemetry_events`) in a single bulk insert.
  Responds `200 OK` with `{"received", "stored", "rejected"}`.
- The `TELEMETRY_ENDPOINT` env var reserves the destination URL for
  redirecting traffic; it is read at startup. See `.env.example`.

### `GET /telemetry/report` — Reporte técnico (Fase 2)

- Query params (optional, ISO 8601): `start_date` and `end_date`. If
  omitted, defaults to the last 7 days UTC (start inclusive, end exclusive).
- Calls the analysis pipeline in `services/telemetry` (6 operational
  metrics: `events_per_day`, `events_per_type_per_day`, `error_rate_daily`,
  `error_rate_by_type`, `api_errors_by_endpoint`, `auth_failure_rate`).
- Response: `{"period": {"from", "to"}, "metrics": {...}}` where the period
  is the resolved UTC window.
- Caching: in-memory **TTL 60 s** (`cache.telemetry_report_cache`). The
  default window is rounded to the minute so repeated requests within the
  TTL hit the cache and do not recompute the pipeline.
- Auth required: valid Bearer token (same as incidents/suppliers).
- Returns `422` on invalid window (`start_date >= end_date`) and `503` if
  `DATABASE_URL` is not configured.

### Dashboard técnico (opcional, `uis/backoffice/telemetry/`)

A minimal technical view that consumes `GET /telemetry/report` and renders
each metric as an HTML table, showing the resolved `period`. It is an
engineering view only (no business metrics).

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
