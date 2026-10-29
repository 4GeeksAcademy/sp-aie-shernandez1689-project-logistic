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

A network cut does not make an open `fetch` stream fail by itself (the TCP
connection stays half-open), so the UI also treats the stream as dead when the
browser fires `offline` or when nothing arrives for 45 s (three missed
`: ping`). The `online` event shortens the pending backoff wait.

The broker is per process: with several workers, each one only sees the
tickets it processes. Run a single worker, or replace the broker with a shared
channel (e.g. PostgreSQL `LISTEN/NOTIFY` or Redis pub/sub).

Run uvicorn with `--timeout-graceful-shutdown 5`: on Python 3.12 shutdown waits
for every open connection, and an SSE stream never closes on its own.

Tests: `tests/pipelines/test_rfp_events.py` (framing, auth, replay, real HTTP)
and `tests/pipelines/test_rfp_intake_ui.py` (Chromium: live arrival, network cut
and API outage → backoff → replay without duplicates, restart → resync).

Manual check:

1. Start the API and open `uis/backoffice/rfp/index.html` signed in; the
   status shows "En vivo".
2. Upload a PDF from another tab or with `curl`: the row appears on top with
   the "Nueva" badge and the notice, and its status advances without reloading.
3. Cut the network (DevTools → Network → Offline) or stop the API: the status
   shows "Reconectando…" and `/rfp/events` retries are spaced 1 s, 2 s, 4 s…
   Back online, the reconnection replays the missed events (`Last-Event-ID`)
   without duplicate rows or re-announcing tickets already seen.
4. Restart it: the reconnection carries `Last-Event-ID`; since the restart
   changes the `epoch`, the server answers `rfp_resync` and the table is
   re-fetched once, with no duplicate rows. Without a restart (network blip)
   the missed events are replayed instead.

## Support agent real-time chat (WebSocket)

Extends `POST /agent/query` (request/response) with a streaming chat over the
same LangGraph agent, `first_line_cx`. Entities follow `company-brifing2.md`:
each conversation is a `chat_session` flow with `flow_id` `chat_xxxxxxxx`, which
is also the session id and the LangGraph `thread_id`. Implementation:
`agent_chat.py` (event bus, sessions, producer) + `routes/agent.py`.

### `POST /agent/sessions` + `WS /agent/ws?token=<JWT>&flow_id=<chat_...>`

- `POST /agent/sessions` (same Bearer JWT as the rest of the backoffice API)
  → `201 {agent_id, flow_id, flow_type: "chat_session", created_at}`.
- The socket needs the JWT in `?token=` (browsers cannot send `Authorization`
  in the handshake) and an existing `flow_id` owned by that user. Otherwise it
  is closed **before any chat event**: `4401` no/invalid JWT, `4400` missing
  `flow_id`, `4404` unknown session (e.g. after a restart), `4403` someone
  else's session, `4410` session cancelled by an operator. Exception: a user
  with a control role may join someone else's session as a read-only observer
  (supervisor): same topic, `pause`/`resume`/`cancel` allowed,
  `send_message`/`interrupt` answered with `chat_error` `read_only`. The UI
  opens it with `?flow_id=chat_...`.
- The first event is always `session_restored`.

Frames are JSON `{"command"|"event": ..., "data": {...}}`; every event carries
`agent_id` and `flow_id`.

| Client → server | `data` | Effect |
|---|---|---|
| `send_message` | `{text}` (1–1000) | New turn. If a reply is streaming, it is interrupted first (`reason: new_input`). |
| `interrupt` | `{}` | Stops the streaming reply (`reason: user_interrupt`). |
| `pause` / `resume` / `cancel` | `{agent_id, flow_id}` | Operator control (briefing §2–§4), roles in `AGENT_CONTROL_ROLES` (default `engineering,operations`); `flow_id` may be another session. |

| Server → client | `data` (besides `agent_id`, `flow_id`) |
|---|---|
| `session_restored` | `flow_type, created_at, messages[], generation: {message_id, paused} \| null` |
| `message_created` | `message` (user message) |
| `generation_started` | `message_id, reply_to` |
| `generation_step` | `message_id, node` (a graph node finished) |
| `token_chunk` | `message_id, token` |
| `generation_completed` | `message_id, content` (final, guardrail-validated answer) |
| `generation_interrupted` | `message_id, content` (partial already shown), `reason`: `new_input \| user_interrupt \| cancelled` |
| `generation_failed` | `message_id, message` |
| `control_applied` | `action, actor_id, timestamp` |
| `chat_session_closed` | `reason` |
| `chat_error` | `code, message` (only to the connection that sent the command) |

Messages: `{message_id, role: user|assistant, content, status, created_at,
reply_to}`, `status` = `complete | streaming | interrupted | failed`.

### Streaming, abort and pub/sub

- Streaming mode: `graph.stream(..., stream_mode=["custom", "updates"])`. The
  LLM is called with httpx (`data/pipelines/rag.py`), not a LangChain chat
  model, so `messages` mode would see no tokens: `generate`/`casual` emit
  them with `get_stream_writer()` only when `configurable.stream_tokens` is
  set (`/agent/query` is unchanged). `updates` gives the steps and the final
  answer.
- Abort: the producer checks a gate before every token and between nodes.
  An interruption marks the generation inactive on the event loop (chunks
  already in flight are dropped, so no `token_chunk` of that `message_id`
  follows `generation_interrupted`) and makes the gate raise `StreamAborted`,
  which closes the streamed LLM HTTP response. It is a stream abort, not a
  LangGraph `interrupt()`. The partial message stays in history as
  `interrupted`; the next turn waits for the aborted run to exit so two runs
  never share the `thread_id`.
- Producer/consumer: each turn runs in a thread (the checkpointer is
  synchronous) and hands chunks to the loop with `call_soon_threadsafe`;
  `ChatService` updates the session history and publishes to the `flow_id`
  topic of `ChatEventBus`. Each socket is just a subscriber, so a dropped
  connection does not stop the turn; on reconnect the snapshot and the
  subscription are taken in the same loop step (nothing lost or duplicated).
  A subscriber that falls 1000 frames behind is closed with `4408` and
  rehydrates on reconnect.
- Limits: history and bus live in the process (one worker; a restart loses
  sessions → `4404`). For several workers, move the topic to Redis pub/sub or
  `LISTEN/NOTIFY` and the history to the database. While paused the provider
  keeps generating into the HTTP buffer; a very long pause may hit its idle
  timeout.

UI: `uis/backoffice/agent-chat/` keeps the `flow_id` in localStorage, renders
tokens live, interrupts by sending while the agent answers (or "Detener
respuesta"), and reconnects with backoff 1 s → 30 s (+ jitter), repainting the
conversation from `session_restored` before accepting new tokens.

Design questions (why WebSockets, several subscribers, abort vs `interrupt()`):
`docs/agent-chat/websocket-design-questions.md`.

Tests: `tests/pipelines/test_agent_chat_ws.py` (event contract, handshake,
supervisor observer,
interrupt mid-reply, reconnect/rehydrate also mid-generation, operator control,
pub/sub bus, LLM streaming and `custom` mode).

### Manual check (done on 2026-10-29)

Real `main:app` + real graph + an OpenAI-compatible stub streaming one token
every 250 ms, driven from Chromium (Playwright) and a raw `websockets` client:

1. Open `uis/backoffice/agent-chat/index.html` signed in: a session is created
   and stored; status "En línea".
2. Ask "¿Qué hora es?": the reply grows token by token (8 → 14 → 17 chars
   sampled every 300 ms).
3. Mid-reply, send "¿Qué hora es en Los Ángeles?": the first reply stops at
   "Son las 10:00 en " with the "Interrumpida" badge and never grows again; a
   new user message and a new reply appear. Raw client: 0 `token_chunk` of the
   original `message_id` after `generation_interrupted`.
4. DevTools offline: status "Reconectando en 2 s…", input disabled; back
   online, `session_restored` repaints the same 4 messages on the same
   `flow_id`.
5. Go offline mid-reply: the server keeps generating; on reconnect the partial
   is restored and the reply finishes with the full text.
6. Reload the page: same `flow_id`, 6 messages restored (not an empty chat).

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
