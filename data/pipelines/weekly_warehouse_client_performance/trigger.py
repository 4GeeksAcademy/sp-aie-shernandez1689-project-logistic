"""Disparo manual del pipeline.

PIPELINE_DESIGN.md Fase 5 §5.2 (Endpoint 2):

- ``trigger_weekly_run(week_start)`` → lanza el flow Prefect
  ``weekly_warehouse_client_performance`` y devuelve el ``run_id``.
- Es la única función que ``services/reporting/`` importa para el endpoint
  ``POST /reporting/pipeline-runs``.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone

from data.pipelines.weekly_warehouse_client_performance.pipeline import (
    weekly_flow,
)
from data.pipelines.weekly_warehouse_client_performance.runs_log import (
    log_run_start,
)

logger = logging.getLogger(__name__)


def trigger_weekly_run(week_start: date | None = None) -> str:
    """Lanza el flow Prefect de desempeño semanal.

    Parameters
    ----------
    week_start : date | None
        Lunes de la semana ISO a procesar. Si es ``None``, se usa la
        última semana ISO completa (comportamiento por defecto del flow).

    Returns
    -------
    str
        UUID de la corrida registrada.
    """
    # Registrar la corrida antes de lanzar
    now_utc = datetime.now(timezone.utc)
    run_id = log_run_start(
        start_window=now_utc,
        end_window=now_utc,
    )

    # Lanzar el flow (ejecución síncrona en este hilo)
    # En producción se recomienda lanzar como Prefect deployment remoto.
    weekly_flow(week_start=week_start)

    logger.info(
        "trigger: flow 'weekly_warehouse_client_performance' launched (run_id=%s, week_start=%s).",
        run_id,
        week_start,
    )
    return run_id