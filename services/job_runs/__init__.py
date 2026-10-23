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
una tabla/columna separada de bloqueo. El lock es job-level: si existe una
fila ``processing`` para el job (p. ej. ``nightly_export``), otra instancia
aborta silenciosamente.

La **idempotencia** se resuelve con la pareja (job_name, target_date):
si existe una fila ``completed`` para esa pareja exacta, el trabajo no se
relanza (solo job_name sin target_date no basta).

Uso típico (desde scripts/):

    from services.job_runs.state import create_run, start_processing, \
        complete_run, fail_run, is_locked, is_completed
    from services.job_runs.database import get_session

    with next(get_session()) as session:
        # Lock vía processing: aborta silenciosamente si otra instancia corre
        if is_locked(session, "nightly_export"):
            sys.exit(0)
        # Idempotencia vía target_date: omite si ya se completó
        if is_completed(session, "nightly_export", target_date):
            sys.exit(0)
        run = create_run(session, "nightly_export", target_date)
        if not start_processing(session, run.id, "nightly_export", target_date):
            sys.exit(0)  # otro proceso ya lo tomó (re-chequeo atómico)
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