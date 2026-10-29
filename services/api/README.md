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

## RFP real-time notifications (SSE)

### `GET /rfp/events`

Server-Sent Events stream that replaces the polling in `uis/backoffice/rfp/`.
Implementation: `rfp_events.py` (broker) + `routes/rfp.py` (endpoint). The
pipeline (`data/pipelines/rfp_intake/db.py`) notifies every ticket insert or
change through `db.add_ticket_listener`, without importing `services/`.

- Auth: same Bearer JWT as the rest of the backoffice API (`401` without it;
  no event is ever sent to an unauthenticated client).
- Headers: `Content-Type: text/event-stream`, `Cache-Control: no-cache`,
  `Connection: keep-alive`, `X-Accel-Buffering: no`.
- Keep-alive: `retry: 3000` and a `: connected` comment on open, then a
  `: ping` comment every 15 s without events.
- Named events (never the generic `message`):

| `event:` | When | `data:` |
| --- | --- | --- |
| `rfp_ticket_created` | New ticket, in its initial status `analizando` | Ticket (BRIEFING §2.3) |
| `rfp_ticket_updated` | Status/reason/metadata change (Parts 1–3) | Ticket (BRIEFING §2.3) |
| `rfp_resync` | Missed events can no longer be replayed | `{"reason": "replay_unavailable"}` |

Ticket payload: `ticket_id`, `rfp_id`, `status`, `reason`, `raw_pdf_path`,
`created_at`, `updated_at` plus the RFP metadata the table shows
(`client_name`, `client_country`, `services_requested`, `monthly_volume`,
`deadline`, `budget_range`, `departments_needed`, `currency`; `null` until the
analysis extracts them).

```text
id: 3f9a1c2e-1
event: rfp_ticket_created
data: {"ticket_id": "rfp_20261029_ab12cd34", "rfp_id": "rfp_20261029_ab12cd34-rfp", "status": "analizando", ...}
```

### Recovery after a disconnection (Last-Event-ID + short replay)

Every frame carries `id: <epoch>-<seq>`. The server keeps the last 200 frames.
When the UI reconnects it sends `Last-Event-ID` and receives only the frames
after it. If the id belongs to another process (`epoch` differs after a
restart) or has left the buffer, the server sends `rfp_resync` and only then
the UI re-fetches `GET /rfp/tickets`. The UI keeps one row per `ticket_id` and
ignores payloads older than what it already shows (`updated_at`), so replays
and resyncs never duplicate a ticket. Reconnection uses exponential backoff
(1 s → 30 s).

The broker is per process: with several workers, each one only sees the
tickets it processes. Run a single worker, or replace the broker with a shared
channel (e.g. PostgreSQL `LISTEN/NOTIFY` or Redis pub/sub).

Run uvicorn with `--timeout-graceful-shutdown 5`: on Python 3.12 shutdown waits
for every open connection, and an SSE stream never closes on its own.

Tests: `tests/pipelines/test_rfp_events.py` (framing, auth, replay, real HTTP)
and `tests/pipelines/test_rfp_intake_ui.py` (Chromium: live arrival, API
outage → backoff → recovery without duplicates).

Manual check:

1. Start the API and open `uis/backoffice/rfp/index.html` signed in; the
   status shows "En vivo".
2. Upload a PDF from another tab or with `curl`: the row appears on top with
   the "Nueva" badge and the notice, and its status advances without reloading.
3. Stop the API: the status shows "Reconectando…" and DevTools → Network shows
   `/rfp/events` retries spaced 1 s, 2 s, 4 s…
4. Restart it: the reconnection carries `Last-Event-ID`; since the restart
   changes the `epoch`, the server answers `rfp_resync` and the table is
   re-fetched once, with no duplicate rows. Without a restart (network blip)
   the missed events are replayed instead.

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
