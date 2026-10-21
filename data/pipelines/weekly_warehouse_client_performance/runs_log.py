"""Log de corridas del pipeline (reporting.pipeline_runs).

PIPELINE_DESIGN.md Fase 3 §3.2 / Fase 4 §4.2:

- ``log_run_start``: Inserta una fila con estado ``running``, devuelve el ``run_id``.
- ``log_run_finish``: Actualiza la fila con estado ``success``/``failed``,
  ``finished_at``, contadores y opcionalmente ``error_message``.

En modo local (sin DATABASE_URL) escribe en ``data/eval/`` como JSONL.
"""

from __future__ import annotations

import json
import logging
import uuid as uuid_mod
from datetime import datetime, timezone
from typing import Any

from sqlmodel import Session, text

from data.pipelines.weekly_warehouse_client_performance import db as pipeline_db
from data.pipelines.weekly_warehouse_client_performance.config import (
    PIPELINE_NAME,
    RUNS_TABLE,
    RUN_STATUS_FAILED,
    RUN_STATUS_RUNNING,
    RUN_STATUS_SUCCESS,
)

logger = logging.getLogger(__name__)


def log_run_start(
    pipeline: str = PIPELINE_NAME,
    start_window: datetime | None = None,
    end_window: datetime | None = None,
) -> str:
    """Registra el inicio de una corrida en ``reporting.pipeline_runs``.

    Parameters
    ----------
    pipeline : str
        Nombre del pipeline (por defecto el de este paquete).
    start_window : datetime | None
        Inicio de la ventana de extracción (UTC).
    end_window : datetime | None
        Fin de la ventana de extracción (UTC).

    Returns
    -------
    str
        UUID de la corrida (como string).
    """
    run_id = str(uuid_mod.uuid4())

    with pipeline_db.session_scope() as session:
        if session is None:
            # Modo local
            _write_local_run(
                {
                    "id": run_id,
                    "pipeline": pipeline,
                    "status": RUN_STATUS_RUNNING,
                    "started_at": datetime.now(timezone.utc).isoformat(),
                    "start_window": start_window.isoformat() if start_window else None,
                    "end_window": end_window.isoformat() if end_window else None,
                }
            )
            logger.info("log_run_start: %s (local mode).", run_id)
            return run_id

        stmt = text(
            f"""
            INSERT INTO {RUNS_TABLE}
                (id, pipeline, status, started_at)
            VALUES
                (:id, :pipeline, :status, NOW())
            """
        )
        session.execute(stmt, {
            "id": run_id,
            "pipeline": pipeline,
            "status": RUN_STATUS_RUNNING,
        })
        session.commit()

    logger.info("log_run_start: %s — %s.", run_id, RUN_STATUS_RUNNING)
    return run_id


def log_run_finish(
    run_id: str,
    success: bool = True,
    processed_events: int = 0,
    rows_upserted: int = 0,
    error_message: str | None = None,
) -> None:
    """Registra la finalización de una corrida.

    Parameters
    ----------
    run_id : str
        UUID de la corrida (devuelto por ``log_run_start``).
    success : bool
        ``True`` si la corrida fue exitosa.
    processed_events : int
        Número de eventos leídos en la extracción.
    rows_upserted : int
        Número de filas insertadas/actualizadas en la carga.
    error_message : str | None
        Mensaje de error si ``success=False``.
    """
    status = RUN_STATUS_SUCCESS if success else RUN_STATUS_FAILED

    with pipeline_db.session_scope() as session:
        if session is None:
            _write_local_run(
                {
                    "id": run_id,
                    "status": status,
                    "finished_at": datetime.now(timezone.utc).isoformat(),
                    "processed_events": processed_events,
                    "rows_upserted": rows_upserted,
                    "error_message": error_message,
                }
            )
            logger.info(
                "log_run_finish: %s — %s (local mode).", run_id, status
            )
            return

        stmt = text(
            f"""
            UPDATE {RUNS_TABLE}
            SET status = :status,
                finished_at = NOW(),
                processed_events = :processed_events,
                rows_upserted = :rows_upserted,
                error_message = :error_message
            WHERE id = :run_id
            """
        )
        session.execute(stmt, {
            "run_id": run_id,
            "status": status,
            "processed_events": processed_events,
            "rows_upserted": rows_upserted,
            "error_message": error_message,
        })
        session.commit()

    logger.info(
        "log_run_finish: %s — %s (events=%d, upserted=%d).",
        run_id,
        status,
        processed_events,
        rows_upserted,
    )


def _write_local_run(record: dict[str, Any]) -> None:
    """Modo local: escribe un registro de corrida a JSONL."""
    out_path = pipeline_db.eval_runs_file()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, default=str) + "\n")