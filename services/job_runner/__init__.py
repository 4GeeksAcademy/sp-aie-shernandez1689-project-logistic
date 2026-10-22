"""Módulo job_runner — control de estado para ejecuciones de pipelines.

Proporciona funciones para crear, actualizar y consultar registros de
ejecución (job_runs), así como dos guardianes de concurrencia:

- ``has_processing_lock``: impide que dos instancias del mismo pipeline
  se ejecuten simultáneamente.
- ``has_completed_for_date``: evita reprocesar fechas ya consolidadas.

Contrato de errores
-------------------
Cualquier excepción no controlada en una corrida (bloque ``run_job``) se
captura, se actualiza el estado del registro a ``failed`` con el mensaje de
error, se propaga al log, y **se relanza** para que el llamador pueda
reaccionar (por ejemplo, devolver un HTTP 500). Así ningún registro queda
en estado ``processing`` tras una ejecución fallida.
"""

from services.job_runner.runner import (
    create_job_run,
    get_job_run,
    get_latest_job_run,
    get_job_runs_for_date,
    has_completed_for_date,
    has_processing_lock,
    run_job,
    update_job_run_status,
)

__all__ = [
    "create_job_run",
    "get_job_run",
    "get_latest_job_run",
    "get_job_runs_for_date",
    "has_completed_for_date",
    "has_processing_lock",
    "run_job",
    "update_job_run_status",
]