"""Tareas asíncronas de Celery para TrackFlow.

Cada tarea es una función decorada con ``@celery_app.task`` que puede
ejecutarse de forma asíncrona por un worker de Celery.

Tareas disponibles:
- ``generate_telemetry_report``: genera el reporte técnico de telemetría
  (la operación más costosa de la API). Ver services/telemetry/analysis.py
  para la lógica interna.

Dead Letter Queue (DLQ):
- Cuando una tarea agota todos los reintentos (max_retries), el hook
  ``on_failure`` de la clase base ``DatabaseTask`` persiste el fallo
  en la tabla ``celery_task_failures`` (PostgreSQL).
"""

from __future__ import annotations

import logging
import sys
import traceback as tb_module
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ── Asegurar que la raíz del monorepo y services/api están en sys.path ──
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

API_DIR = Path(__file__).resolve().parent / "api"
if str(API_DIR) not in sys.path:
    sys.path.insert(0, str(API_DIR))

from celery import Task
from sqlmodel import Session

from db_telemetry import engine as telemetry_engine
from models import CeleryTaskFailure
from services.celery_app import celery_app
from services.telemetry import build_telemetry_report

logger = logging.getLogger(__name__)


class DatabaseTask(Task):
    """Task base con:

    - ``on_failure``: persiste el fallo definitivo en la tabla
      ``celery_task_failures`` (Dead Letter Queue) cuando se agotan
      los reintentos.
    - ``after_return``: cierra la sesión de base de datos.
    """

    _session: Session | None = None

    def on_failure(self, exc, task_id, args, kwargs, einfo):
        """Hook ejecutado cuando la tarea falla definitivamente.

        Se invoca solo cuando se han agotado todos los reintentos
        (``max_retries``). Persiste el fallo en la DLQ.
        """
        task_name = self.name or "unknown"

        logger.error(
            "Task %s (%s) failed permanently after %d retries: %s",
            task_name,
            task_id,
            self.max_retries or 0,
            exc,
        )

        if telemetry_engine is None:
            logger.warning("Cannot persist DLQ entry: DATABASE_URL not configured")
            return

        try:
            with Session(telemetry_engine) as session:
                record = CeleryTaskFailure(
                    task_id=task_id,
                    task_name=task_name,
                    attempt=(self.request.retries if hasattr(self, "request") else 0) + 1,
                    error=str(exc),
                    traceback=tb_module.format_exc() if einfo else None,
                    failed_at=datetime.now(timezone.utc),
                )
                session.add(record)
                session.commit()
                logger.info(
                    "DLG entry recorded for task %s (%s)",
                    task_name,
                    task_id,
                )
        except Exception as persist_exc:
            logger.error(
                "Failed to persist DLQ entry for task %s: %s",
                task_id,
                persist_exc,
            )

    def after_return(self, status, retval, task_id, args, kwargs, einfo):
        """Cierra la sesión después de cada ejecución."""
        if self._session is not None:
            self._session.close()
            self._session = None


@celery_app.task(
    base=DatabaseTask,
    bind=True,
    name="generate_telemetry_report",
    max_retries=3,
    acks_late=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=600,  # 10 minutos como tope máximo
    retry_jitter=True,
)
def generate_telemetry_report(
    self,
    start_date_iso: str,
    end_date_iso: str,
) -> dict[str, Any]:
    """Genera el reporte completo de telemetría de forma asíncrona.

    Reemplaza la llamada síncrona en ``GET /telemetry/report``.

    Reintentos con backoff exponencial:
        - ``max_retries=3`` (4 intentos en total)
        - ``retry_backoff=True`` → countdown = 2^retry * BASE_RETRY_DELAY
          (30s → 60s → 120s → 240s)
        - ``retry_jitter=True`` → añade dispersión aleatoria para evitar
          tormentas de reintentos (thundering herd).

    Dead Letter Queue:
        - Si tras 3 reintentos sigue fallando, el hook ``on_failure`` de
          ``DatabaseTask`` persiste el error en ``celery_task_failures``.

    Args:
        start_date_iso: Inicio de la ventana (ISO 8601).
        end_date_iso: Fin exclusivo de la ventana (ISO 8601).

    Returns:
        Dict con el reporte completo::

            {
                "period": {"from": "...", "to": "..."},
                "metrics": { <métrica>: [...] },
            }
    """
    if telemetry_engine is None:
        raise RuntimeError(
            "DATABASE_URL no está configurada; no se puede generar el reporte."
        )

    start = datetime.fromisoformat(start_date_iso)
    end = datetime.fromisoformat(end_date_iso)

    logger.info(
        "Generating telemetry report (task=%s, attempt=%d) for window %s – %s",
        self.request.id,
        self.request.retries + 1 if hasattr(self, "request") else 1,
        start_date_iso,
        end_date_iso,
    )

    with Session(telemetry_engine) as session:
        metrics = build_telemetry_report(session, start, end)

    logger.info(
        "Telemetry report generated successfully (task=%s, attempt=%d)",
        self.request.id,
        self.request.retries + 1 if hasattr(self, "request") else 1,
    )

    return {
        "period": {
            "from": start_date_iso,
            "to": end_date_iso,
        },
        "metrics": metrics,
    }


@celery_app.task(name="ping")
def ping() -> dict[str, Any]:
    """Tarea simple de verificación: devuelve pong.

    Útil para comprobar que el worker y el broker responden.
    """
    return {"status": "pong", "timestamp": datetime.now(timezone.utc).isoformat()}