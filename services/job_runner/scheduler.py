#!/usr/bin/env python
"""Disparador (scheduler) nocturno para pipelines — script independiente.

NO debe ejecutarse dentro del proceso FastAPI (sin APScheduler,
``@repeat_every`` ni hooks ``lifespan``).

Uso
----
::

    # Ejecución normal (procesa última semana ISO completa)
    python services/job_runner/scheduler.py

    # Prueba con fecha específica
    TARGET_DATE=2026-W28 python services/job_runner/scheduler.py

    # Prueba con fecha ISO estricta
    TARGET_DATE=2026-07-13 python services/job_runner/scheduler.py

Variables de entorno
--------------------
TARGET_DATE : str, opcional
    Fecha objetivo en formato ISO (``YYYY-MM-DD``) o semana ISO
    (``YYYY-WNN``). Si se omite, se procesa la última semana ISO
    completa (la semana anterior a la actual).

Comportamiento
--------------
1. Verifica que ningún otro worker tenga el candado (``has_processing_lock``).
2. Comprueba que la fecha no haya sido ya procesada exitosamente
   (``has_completed_for_date``).
3. Crea un registro ``pending`` en ``job_runs``.
4. Ejecuta el pipeline bajo el ciclo de vida de ``run_job``.
5. Si falla, el estado se actualiza a ``failed`` y el error se propaga
   al log — nunca queda en ``processing``.

Diseño
------
- Script independiente, orientado a ser ejecutado por ``cron`` del SO
  o por un contenedor scheduler dedicado en Docker/Kubernetes.
- Comparte la misma base de datos PostgreSQL vía ``DATABASE_URL``.
- Usa el módulo ``job_runner`` para el ciclo de vida de ejecuciones.
"""

from __future__ import annotations

import logging
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Añadir raíz del monorepo al sys.path (mismo patrón que tests/ y scripts/)
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from services.job_runner.db import get_engine, init_db  # noqa: E402
from services.job_runner.models import JobRunStatus  # noqa: E402
from services.job_runner.runner import (  # noqa: E402
    create_job_run,
    has_completed_for_date,
    has_processing_lock,
    run_job,
)
from sqlmodel import Session  # noqa: E402

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------

PIPELINE_NAME = "weekly_warehouse_client_performance"

logger = logging.getLogger("job_runner.scheduler")

# ── Helpers ─────────────────────────────────────────────────────────────────


def _iso_monday_from_date(d: date) -> date:
    """Devuelve el lunes de la semana ISO que contiene *d*."""
    iso_year, iso_week, _ = d.isocalendar()
    return date.fromisocalendar(iso_year, iso_week, 1)


def resolve_target_week(target_date_str: str | None) -> date:
    """Resuelve la fecha objetivo (lunes de la semana ISO a procesar).

    Si ``target_date_str`` es ``None``, se usa la última semana ISO completa
    (la semana anterior a la actual). El formato aceptado es ``YYYY-MM-DD``
    o ``YYYY-WNN`` (ISO week).

    Args:
        target_date_str: Fecha ISO opcional (``YYYY-MM-DD`` o ``YYYY-WNN``).

    Returns:
        ``date`` del lunes de la semana ISO a procesar.
    """
    if target_date_str is None:
        # Última semana ISO completa = la semana anterior a la actual
        today = datetime.now(timezone.utc).date()
        this_monday = _iso_monday_from_date(today)
        last_monday = this_monday - timedelta(days=7)
        logger.info("No TARGET_DATE set — using last complete ISO week: %s", last_monday)
        return last_monday

    # Intentar parsear como YYYY-MM-DD
    try:
        d = date.fromisoformat(target_date_str)
        monday = _iso_monday_from_date(d)
        logger.info("TARGET_DATE=%s → ISO week starting %s", target_date_str, monday)
        return monday
    except ValueError:
        pass

    # Intentar parsear como YYYY-WNN (ISO week)
    import re  # noqa: E402

    match = re.match(r"^(\d{4})-W(\d{1,2})$", target_date_str)
    if match:
        year = int(match.group(1))
        week = int(match.group(2))
        monday = date.fromisocalendar(year, week, 1)
        logger.info("TARGET_DATE=%s → ISO week starting %s", target_date_str, monday)
        return monday

    raise ValueError(
        f"TARGET_DATE={target_date_str!r} no es una fecha ISO válida "
        f"(formatos aceptados: YYYY-MM-DD o YYYY-WNN)"
    )


# ── Lógica principal ────────────────────────────────────────────────────────


def run_scheduler(target_date_str: str | None = None) -> None:
    """Ejecuta el ciclo completo del scheduler.

    1. Resuelve la semana objetivo.
    2. Inicializa la base de datos (crea tablas si no existen).
    3. Verifica el candado de concurrencia (``has_processing_lock``).
    4. Verifica que la fecha no esté ya completada (``has_completed_for_date``).
    5. Crea un registro ``pending`` y lo ejecuta con ``run_job``.

    Args:
        target_date_str: Fecha ISO opcional (``YYYY-MM-DD`` o ``YYYY-WNN``).
            Si es ``None``, se lee de la variable de entorno ``TARGET_DATE``.
            Si ambas son ``None``, se usa la última semana ISO completa.

    Raises:
        SystemExit: Si el pipeline ya está en ejecución o la fecha ya
            fue procesada.
    """
    # Importar trigger dentro de la función (lazy) para que el scheduler
    # pueda importarse sin que todas las dependencias del pipeline estén
    # instaladas (Prefect, pandas, etc.).
    from data.pipelines.weekly_warehouse_client_performance.trigger import (  # noqa: E402
        trigger_weekly_run,
    )

    # 1. Resolver semana objetivo
    target_week = resolve_target_week(target_date_str)
    date_str = target_week.isoformat()

    start_seq = "=" * 40
    logger.info("%s SCHEDULER START pipeline=%s week=%s %s", start_seq, PIPELINE_NAME, date_str, start_seq)

    # 2. Inicializar conexión a BD
    logger.info("SCHEDULER INIT | pipeline=%s", PIPELINE_NAME)
    init_db()
    engine = get_engine()

    with Session(engine) as session:
        # 3. Verificar candado de concurrencia
        logger.info("SCHEDULER GUARD | pipeline=%s check=processing_lock", PIPELINE_NAME)
        if has_processing_lock(session, PIPELINE_NAME):
            logger.error(
                "SCHEDULER ABORT | pipeline=%s reason=concurrent_lock status=PROCESSING",
                PIPELINE_NAME,
            )
            sys.exit(1)

        # 4. Verificar que la fecha no esté ya completada
        logger.info("SCHEDULER GUARD | pipeline=%s check=date_completed date=%s", PIPELINE_NAME, date_str)
        if has_completed_for_date(session, PIPELINE_NAME, date_str):
            logger.info(
                "SCHEDULER SKIP | pipeline=%s date=%s status=SUCCESS — already processed",
                PIPELINE_NAME,
                date_str,
            )
            logger.info("%s SCHEDULER FINISH pipeline=%s status=skipped %s", start_seq, PIPELINE_NAME, start_seq)
            return

        # 5. Crear registro pending
        logger.info("SCHEDULER CREATE | pipeline=%s date=%s status=PENDING", PIPELINE_NAME, date_str)
        run = create_job_run(
            session,
            pipeline=PIPELINE_NAME,
            date_from=date_str,
            date_to=None,  # trigger_weekly_run maneja su propia ventana
        )
        run_id = run.id
        logger.info("SCHEDULER CREATED | pipeline=%s id=%s status=PENDING", PIPELINE_NAME, run_id)

        # 6. Ejecutar pipeline bajo el ciclo de vida de run_job
        logger.info(
            "SCHEDULER EXEC | pipeline=%s id=%s week=%s status=TRIGGERING",
            PIPELINE_NAME, run_id, date_str,
        )

        run_job(
            session,
            run_id,
            trigger_weekly_run,
            week_start=target_week,
        )

        logger.info(
            "%s SCHEDULER FINISH pipeline=%s id=%s week=%s status=%s %s",
            start_seq, PIPELINE_NAME, run_id, date_str, JobRunStatus.SUCCESS.value, start_seq,
        )


# ── Entry point ─────────────────────────────────────────────────────────────


def main() -> None:
    """Entry point del scheduler — lee ``TARGET_DATE`` de entorno y ejecuta."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    target_date = os.environ.get("TARGET_DATE")
    if target_date:
        logger.info("TARGET_DATE override: %s", target_date)

    try:
        run_scheduler(target_date_str=target_date)
    except SystemExit:
        raise  # respetar sys.exit(1) por lock, sys.exit(2) por error
    except Exception:
        logger.exception("SCHEDULER CRASH | pipeline=%s status=EXCEPTION — unexpected error", PIPELINE_NAME)
        sys.exit(2)


if __name__ == "__main__":
    main()