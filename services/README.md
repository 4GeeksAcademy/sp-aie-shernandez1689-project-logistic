# `services/` — Backend Services

This folder contains all the backend services for the TrackFlow monorepo:
**REST APIs**, **background workers** (Celery), and **analysis packages**.

> _Versión en español: [README.es.md](./README.es.md)_

---

## Services overview

| Directory              | Type              | Description                                                  |
| ---------------------- | ----------------- | ------------------------------------------------------------ |
| `api/`                 | FastAPI REST API  | Monorepo API: incidents, suppliers, telemetry, async tasks   |
| `user-api/`            | FastAPI REST API  | User authentication API (register, login, forgot/reset pwd)  |
| `celery_app.py`        | Celery app        | Celery application definition (broker, backend, config)      |
| `celery_tasks.py`      | Celery tasks      | Asynchronous task implementations (telemetry report, ping)   |
| `telemetry/`           | Python package    | Telemetry analysis pipeline (Pandas-based metric functions)   |
| `reporting/`           | FastAPI router    | Weekly warehouse/client performance reports (pipeline runner) |
| `job_runner/`          | Scripts           | Cron-based job scheduler (SO cron, not Celery)               |

---

## 1. Celery Worker (asynchronous task processor)

The Celery worker is an **independent process** that runs background tasks
enqueued by the API (or any other producer). It does **not** share the
FastAPI process — it runs as its own OS process, decoupled from the API's
lifecycle.

### Architecture

```text
┌──────────────┐     send_task()     ┌──────────────┐     ┌─────────────────┐
│  FastAPI API │ ──────────────────▶ │   Redis      │ ───│  Celery Worker  │
│  (producer)  │   202 + task_id     │  (broker)    │    │  (consumer)     │
└──────────────┘                     └──────────────┘    └─────────────────┘
                                          │                    │
                                    ┌─────▼──────┐       ┌────▼────┐
                                    │  Flower    │       │ Redis   │
                                    │ (monitor)  │       │(backend)│
                                    └────────────┘       └─────────┘
```

- **Producer**: The FastAPI endpoints enqueue tasks via `celery_app.send_task()`
  and return immediately with `202 Accepted` + `{"task_id": "..."}`.
- **Broker**: Redis stores the task queue until the worker picks it up.
- **Worker**: The Celery worker process consumes tasks, executes them, and
  stores the result back in Redis (the Celery backend).
- **Monitor**: Flower provides a web UI to inspect workers, queues, and tasks.

### Prerequisites

- **Python** >= 3.12
- **Redis** running and accessible via `REDIS_URL` (default: `redis://redis:6379/0`)
- **Dependencies** installed (see [pyproject.toml](../pyproject.toml) or
  run `pip install -e .` from the repo root)

### How to start the worker

#### Option A: Docker Compose (recommended)

```bash
# Start the whole stack (API + worker + Redis + Flower + scheduler)
docker compose up -d

# Start only the worker (if the stack is already up)
docker compose up -d worker

# Scale to multiple workers
docker compose up -d --scale worker=4 worker
```

The worker runs via the [infra/worker.Dockerfile](../infra/worker.Dockerfile),
which installs all monorepo dependencies and starts Celery with:

```text
celery -A services.celery_app worker --loglevel=info
```

#### Option B: Local CLI (development)

```bash
# From the repository root, with dependencies installed:
celery -A services.celery_app worker --loglevel=info

# With a custom concurrency (number of worker processes):
celery -A services.celery_app worker --loglevel=info -c 4

# With a specific queue (if using custom routing):
celery -A services.celery_app worker --loglevel=info -Q default,telemetry
```

> **Tip**: If you get import errors, make sure the repo root is in your
> `PYTHONPATH` or run `pip install -e .` first.

#### Option C: Docker CLI (standalone container)

```bash
docker build -t trackflow-worker -f infra/worker.Dockerfile .
docker run --rm -e REDIS_URL=redis://redis:6379/0 trackflow-worker
```

### How to stop the worker

#### Graceful shutdown

```bash
# If running in a terminal: press Ctrl+C
# The worker will finish its current task and shut down.

# If running via Docker Compose:
docker compose stop worker

# Or remove the container entirely:
docker compose down worker
```

#### Remote shutdown (from another terminal)

```bash
# Send shutdown signal to all workers
celery -A services.celery_app control shutdown

# Revoke a specific task (stop it if it hasn't started yet)
celery -A services.celery_app revoke <task_id>
```

### How to verify the worker is running

#### Ping the worker via Celery inspect

```bash
celery -A services.celery_app inspect ping
# Expected output: {"celery@<hostname>": {"ok": "pong"}}
```

#### Enqueue a ping task manually

```bash
celery -A services.celery_app call ping
# Returns the task UUID. Check result via:
celery -A services.celery_app result <task_id>
# Expected: {"status": "pong", "timestamp": "2026-..."}
```

#### Check worker status

```bash
# List active queues
celery -A services.celery_app inspect active_queues

# List registered tasks
celery -A services.celery_app inspect registered

# Check worker stats (concurrency, load, etc.)
celery -A services.celery_app inspect stats
```

#### Via the API

```bash
# Enqueue a telemetry report
curl -X GET http://localhost:8000/telemetry/report \
  -H "Authorization: Bearer <your-token>"
# → 202 {"task_id": "uuid-..."}

# Check task status
curl http://localhost:8000/tasks/<task_id> \
  -H "Authorization: Bearer <your-token>"
# → {"task_id": "...", "status": "success", "result": {...}}
```

### Monitoring with Flower

Flower is included in `docker-compose.yml` and starts automatically.
Access the dashboard at **http://localhost:5555**.

Flower shows:
- Active workers and their status
- Task queues and message rates
- Task history (success/failure, timing)
- The ability to revoke or retry tasks

```bash
# Start Flower manually (if not using docker-compose)
celery -A services.celery_app flower \
  --broker=redis://redis:6379/0 \
  --port=5555
```

### Tasks available

| Task name                    | Description                                      |
| ---------------------------- | ------------------------------------------------ |
| `generate_telemetry_report`  | Generates the full telemetry report for a window |
| `ping`                       | Health-check task, returns `{"status": "pong"}` |

### Dead Letter Queue (DLQ)

When a task exhausts all retries (`max_retries=3` for telemetry report),
the `DatabaseTask` base class persists the failure details into the
`celery_task_failures` table (PostgreSQL). This allows operators to
review and replay failed tasks without losing error context.

---

## 2. API — Monorepo REST API (`services/api/`)

The main API for TrackFlow operations. Built with **FastAPI**.

### Endpoints

| Endpoint                          | Method | Description                              |
| --------------------------------- | ------ | ---------------------------------------- |
| `/incidents/...`                  | GET    | Incident management                      |
| `/suppliers/...`                  | GET    | Supplier directory                       |
| `/telemetry/events`               | POST   | Ingest raw telemetry events              |
| `/telemetry/report`               | GET    | Enqueue async telemetry report → 202     |
| `/tasks/{task_id}`                | GET    | Poll Celery task status / result         |
| `/reporting/weekly-warehouse-...` | GET    | Weekly warehouse/client performance KPIs |
| `/reporting/pipeline-runs/latest` | GET    | Latest pipeline run metadata             |
| `/reporting/pipeline-runs`        | POST   | Trigger a pipeline run manually          |

### How to run (development)

```bash
cd services/api
uvicorn main:app --reload --port 8000
```

### Async task flow

1. `GET /telemetry/report` resolves the time window, calls
   `celery_app.send_task("generate_telemetry_report", args=[...])`,
   and returns **202 Accepted** with `{"task_id": "..."}`.
2. The **Celery worker** (running independently) picks up the task,
   executes `generate_telemetry_report`, and stores the result in Redis.
3. The client polls `GET /tasks/{task_id}` to retrieve the result when
   `status` becomes `"success"` or `"failure"`.

---

## 3. User API (`services/user-api/`)

Authentication service built with FastAPI. Handles user registration,
login, forgot/reset password flows. Uses JWT tokens and TinyDB (file-based).

```bash
cd services/user-api
uvicorn app.main:app --reload --port 8001
```

---

## 4. Telemetry Analysis Package (`services/telemetry/`)

Pure-Pandas metric functions for the telemetry report pipeline. Each
function loads events from PostgreSQL, transforms them with Pandas, and
returns JSON-serializable dicts.

See [`services/telemetry/__init__.py`](./telemetry/__init__.py) for the
public API (`build_telemetry_report`, `events_per_day`, etc.).

---

## 5. Reporting Router (`services/reporting/`)

Weekly warehouse/client performance reports. Runs as a mounted router
inside the main API. See `data/pipelines/PIPELINE_DESIGN.md` for the
exact contract.

---

## 6. Job Runner / Scheduler (`services/job_runner/`)

Cron-based pipeline scheduler. **This is not Celery** — it uses the
OS cron daemon to trigger weekly pipeline runs. See
[infra/scheduler.Dockerfile](../infra/scheduler.Dockerfile) and
[infra/crontab](../infra/crontab).

---

## Environment variables reference

| Variable         | Default                       | Description                              |
| ---------------- | ----------------------------- | ---------------------------------------- |
| `REDIS_URL`      | `redis://redis:6379/0`        | Redis connection for Celery broker/be    |
| `DATABASE_URL`   | *(none)*                      | PostgreSQL connection (Supabase)         |
| `FLOWER_PORT`    | `5555`                        | Flower web UI port                       |
| `REDIS_PORT`     | `6379`                        | Redis server port (docker-compose)       |
| `JWT_SECRET_KEY` | *(required)*                  | Secret for JWT signing                   |
| `CORS_ALLOW_ORIGINS` | `*`                      | CORS allowed origins                     |

