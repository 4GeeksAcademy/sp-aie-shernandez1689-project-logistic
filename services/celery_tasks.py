"""Tareas asíncronas de Celery para TrackFlow.

Cada tarea es una función decorada con ``@celery_app.task`` que puede
ejecutarse de forma asíncrona por un worker de Celery.

Tareas disponibles:
- ``generate_telemetry_report``: genera el reporte técnico de telemetría
  (la operación más costosa de la API). Ver services/telemetry/analysis.py
  para la lógica interna.
"""

from __future__ import annotations

import logging
import sys
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
from services.celery_app import celery_app
from services.telemetry import build_telemetry_report

logger = logging.getLogger(__name__)


class DatabaseTask(Task):
    """Task base que maneja la sesión de base de datos."""

    _session: Session | None = None

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
    default_retry_delay=30,  # segundos antes de reintentar
    acks_late=True,  # no perder tarea si el worker muere
)
def generate_telemetry_report(
    self,
    start_date_iso: str,
    end_date_iso: str,
) -> dict[str, Any]:
    """Genera el reporte completo de telemetría de forma asíncrona.

    Reemplaza la llamada síncrona en ``GET /telemetry/report``.

    Args:
        start_date_iso: Inicio de la ventana (ISO 8601).
        end_date_iso: Fin exclusivo de la ventana (ISO 8601).

    Returns:
        Dict con el reporte completo::

            {
                "period": {"from": "...", "to": "..."},
                "metrics": { <métrica>: [...] },
            }

    Raises:
        Exception: Reintenta hasta 3 veces si falla la conexión a DB.
    """
    if telemetry_engine is None:
        raise RuntimeError("DATABASE_URL no está configurada; no se puede generar el reporte.")

    start = datetime.fromisoformat(start_date_iso)
    end = datetime.fromisoformat(end_date_iso)

    logger.info(
        "Generating telemetry report (task=%s) for window %s – %s",
        self.request.id,
        start_date_iso,
        end_date_iso,
    )

    try:
        with Session(telemetry_engine) as session:
            metrics = build_telemetry_report(session, start, end)
    except Exception as exc:
        logger.warning("Telemetry report generation failed, retrying: %s", exc)
        raise self.retry(exc=exc) from exc

    logger.info("Telemetry report generated successfully (task=%s)", self.request.id)

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