"""Tareas asíncronas de Celery para TrackFlow.

Cada tarea es una función decorada con ``@celery_app.task`` que puede
ejecutarse de forma asíncrona por un worker de Celery.

Observabilidad
--------------
- Señales globales (``task_prerun``, ``task_postrun``, ``task_failure``)
  registran automáticamente **toda** tarea con: ``task_id``, intento,
  estado resultante y duración de la ejecución.
- Los fallos registran adicionalmente el mensaje de error completo y el
  traceback.
- La configuración ``task_track_started=True`` en ``celery_app.py``
  permite a Flower y al endpoint ``GET /tasks/{task_id}`` ver el estado
  ``started`` mientras la tarea se ejecuta.

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
import time as _time
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

from celery import Task, signals
from sqlmodel import Session

from db_telemetry import engine as telemetry_engine
from models import CeleryTaskFailure
from services.celery_app import celery_app
from services.telemetry import build_telemetry_report

logger = logging.getLogger(__name__)


# =========================================================================
# Observabilidad: señales globales para toda tarea Celery
# =========================================================================
# Se almacena el timestamp de inicio por task_id para calcular duración.
# El diccionario es por-proceso (seguro en modo prefork); en workers
# multithread las colisiones son estadísticamente irrelevantes para logs.
_task_start_times: dict[str, float] = {}


@signals.task_prerun.connect
def _log_task_prerun(task_id: str, task, args, kwargs, **kw) -> None:
    """Registra el inicio de TODA tarea: task_id, nombre, intento, args."""
    _task_start_times[task_id] = _time.monotonic()
    task_name = task.name if task else "unknown"
    retry = getattr(task.request, "retries", 0) if task else 0
    logger.info(
        "TASK_INIT task_id=%s task_name=%s attempt=%d args=%s kwargs=%s",
        task_id,
        task_name,
        retry + 1,
        args,
        kwargs,
    )


@signals.task_postrun.connect
def _log_task_postrun(
    task_id: str,
    task,
    retval,
    state: str,
    **kw,
) -> None:
    """Registra la finalización de TODA tarea: estado + duración (ms)."""
    start = _task_start_times.pop(task_id, None)
    task_name = task.name if task else "unknown"
    if start is not None:
        duration_ms = (_time.monotonic() - start) * 1000
        logger.info(
            "TASK_FINISH task_id=%s task_name=%s state=%s duration_ms=%.1f",
            task_id,
            task_name,
            state,
            duration_ms,
        )
    else:
        logger.info(
            "TASK_FINISH task_id=%s task_name=%s state=%s duration_ms=?",
            task_id,
            task_name,
            state,
        )


@signals.task_failure.connect
def _log_task_failure(
    task_id: str,
    exception: Exception,
    args,
    kwargs,
    traceback,
    einfo,
    **kw,
) -> None:
    """Registra el fallo de TODA tarea: error completo + duración (ms)."""
    task = kw.get("sender")
    task_name = task.name if task else "unknown"
    start = _task_start_times.pop(task_id, None)
    error_msg = f"{type(exception).__name__}: {exception}"

    if start is not None:
        duration_ms = (_time.monotonic() - start) * 1000
        logger.error(
            "TASK_FAILURE task_id=%s task_name=%s error=%s duration_ms=%.1f",
            task_id,
            task_name,
            error_msg,
            duration_ms,
        )
    else:
        logger.error(
            "TASK_FAILURE task_id=%s task_name=%s error=%s duration_ms=?",
            task_id,
            task_name,
            error_msg,
        )

    # Traceback completo en línea separada para evitar truncamiento
    if traceback:
        try:
            tb_str = "".join(tb_module.format_tb(traceback))
            logger.error(
                "TASK_FAILURE_TRACEBACK task_id=%s task_name=%s\n%s",
                task_id,
                task_name,
                tb_str,
            )
        except Exception:
            logger.error(
                "TASK_FAILURE_TRACEBACK task_id=%s task_name=%s (unavailable)",
                task_id,
                task_name,
            )
    elif einfo is not None:
        try:
            logger.error(
                "TASK_FAILURE_TRACEBACK task_id=%s task_name=%s\n%s",
                task_id,
                task_name,
                einfo.traceback,
            )
        except Exception:
            pass


class DatabaseTask(Task):
    """Task base con:

    - ``on_failure``: persiste el fallo definitivo en la tabla
      ``celery_task_failures`` (Dead Letter Queue) cuando se agotan
      los reintentos. Registra además el error completo con duración.
    - ``after_return``: cierra la sesión de base de datos.
    """

    _session: Session | None = None

    def on_failure(self, exc, task_id, args, kwargs, einfo):
        """Hook ejecutado cuando la tarea falla definitivamente.

        Se invoca solo cuando se han agotado todos los reintentos
        (``max_retries``). Persiste el fallo en la DLQ y registra
        el error completo con duración de ejecución.
        """
        task_name = self.name or "unknown"
        attempt = (self.request.retries if hasattr(self, "request") else 0) + 1
        start = _task_start_times.pop(task_id, None)
        error_msg = f"{type(exc).__name__}: {exc}"

        if start is not None:
            duration_ms = (_time.monotonic() - start) * 1000
            logger.error(
                "DLQ_FAILURE task_id=%s task_name=%s attempt=%d error=%s duration_ms=%.1f",
                task_id,
                task_name,
                attempt,
                error_msg,
                duration_ms,
            )
        else:
            logger.error(
                "DLQ_FAILURE task_id=%s task_name=%s attempt=%d error=%s duration_ms=?",
                task_id,
                task_name,
                attempt,
                error_msg,
            )

        # Traceback completo
        if einfo is not None:
            tb_str = str(einfo.traceback) if hasattr(einfo, "traceback") else tb_module.format_exc()
            logger.error(
                "DLQ_FAILURE_TRACEBACK task_id=%s task_name=%s\n%s",
                task_id,
                task_name,
                tb_str,
            )

        # ── Persistir en Dead Letter Queue (PostgreSQL) ──────────
        if telemetry_engine is None:
            logger.warning(
                "DLQ_SKIP_PERSIST task_id=%s task_name=%s reason=DATABASE_URL not configured",
                task_id,
                task_name,
            )
            return

        try:
            with Session(telemetry_engine) as session:
                record = CeleryTaskFailure(
                    task_id=task_id,
                    task_name=task_name,
                    attempt=attempt,
                    error=error_msg,
                    traceback=tb_module.format_exc() if einfo else None,
                    failed_at=datetime.now(timezone.utc),
                )
                session.add(record)
                session.commit()
                logger.info(
                    "DLQ_PERSIST_OK task_id=%s task_name=%s",
                    task_id,
                    task_name,
                )
        except Exception as persist_exc:
            logger.error(
                "DLQ_PERSIST_FAILED task_id=%s task_name=%s error=%s",
                task_id,
                task_name,
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
        "REPORT_WINDOW task_id=%s window_start=%s window_end=%s",
        self.request.id,
        start_date_iso,
        end_date_iso,
    )

    with Session(telemetry_engine) as session:
        metrics = build_telemetry_report(session, start, end)

    logger.info(
        "REPORT_METRICS_GENERATED task_id=%s metric_count=%d",
        self.request.id,
        len(metrics) if isinstance(metrics, dict) else 0,
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