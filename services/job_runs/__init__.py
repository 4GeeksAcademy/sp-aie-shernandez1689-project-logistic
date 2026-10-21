"""Módulo de control de ejecuciones de procesos en segundo plano.

DEV-53 — Script nocturno de telemetría.

Capas del módulo:
    - ``models.py``: SQLModel ``JobRun`` + modelos Pydantic de request/response.
    - ``state.py``: Máquina de estados (create, lock, complete, fail).
    - ``database.py``: Conexión PostgreSQL (reutiliza patrón del monorepo).

La máquina de estados sigue el ciclo:
    pending → processing → completed
                         ↘ failed

El estado ``processing`` actúa como **distributed lock** — no se necesita
una tabla/columna separada de bloqueo.

Uso típico (desde scripts/):

    from services.job_runs.state import create_run, start_processing, \
        complete_run, fail_run
    from services.job_runs.database import get_session

    with next(get_session()) as session:
        run = create_run(session, "nightly_export", "2026-10-20")
        if not start_processing(session, run.id):
            sys.exit(0)  # otro proceso ya lo tomó
        try:
            # ... hacer trabajo ...
            complete_run(session, run.id)
        except Exception as exc:
            fail_run(session, run.id, str(exc))
            raise
"""

from services.job_runs.models import JobRun, JobRunStatus, JobRunCreate, JobRunResponse
from services.job_runs.state import (
    complete_run,
    create_run,
    fail_run,
    get_latest_run,
    get_run,
    get_runs_for_date,
    is_completed,
    is_locked,
    start_processing,
)

__all__ = [
    "JobRun",
    "JobRunStatus",
    "JobRunCreate",
    "JobRunResponse",
    "create_run",
    "start_processing",
    "complete_run",
    "fail_run",
    "get_run",
    "get_latest_run",
    "get_runs_for_date",
    "is_locked",
    "is_completed",
]