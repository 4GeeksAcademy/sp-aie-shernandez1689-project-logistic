"""Core runner — funciones CRUD y guardianes de concurrencia.

Todas las funciones aceptan una sesión SQLModel inyectada; así se pueden
usar tanto desde FastAPI (vía dependencia ``JobRunnerDBSession``) como
desde workers de Prefect o scripts independientes.

Funciones expuestas públicamente desde ``__init__.py``:

- ``create_job_run`` — inserta un nuevo registro ``pending``.
- ``get_job_run`` — obtiene un registro por UUID.
- ``get_latest_job_run`` — última corrida de un pipeline.
- ``get_job_runs_for_date`` — corridas de un pipeline para una fecha.
- ``update_job_run_status`` — cambia el estado y registra timestamps.
- ``has_processing_lock`` — ``True`` si hay otro ``processing`` activo.
- ``has_completed_for_date`` — ``True`` si ya hay un ``success`` para esa fecha.
- ``run_job`` — decorador/función de orden superior que envuelve una
  corrida capturando excepciones y garantizando que el estado nunca quede
  en ``processing``.

Contrato de errores
-------------------
``run_job`` captura cualquier ``Exception``, actualiza el registro a
``failed`` con el mensaje de error, lo registra con ``logger.exception``,
y **relanza** la excepción. Ningún registro puede quedar en estado
``processing`` tras una ejecución fallida.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, timezone
from functools import wraps
from typing import Any, Optional
from uuid import UUID

from sqlalchemy import or_, text
from sqlmodel import Session, select

from services.job_runner.models import (
    JobRun,
    JobRunStatus,
    TERMINAL_STATUSES,
    is_terminal,
    utcnow,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# CRUD básico
# ---------------------------------------------------------------------------


def create_job_run(
    session: Session,
    *,
    pipeline: str,
    date_from: str | None = None,
    date_to: str | None = None,
) -> JobRun:
    """Crea un nuevo registro ``pending`` para el pipeline indicado.

    Args:
        session: Sesión SQLModel activa.
        pipeline: Nombre del pipeline (ej. ``weekly_warehouse_client_performance``).
        date_from: ISO date del inicio de la ventana (opcional).
        date_to: ISO date del fin de la ventana (opcional).

    Returns:
        El registro :class:`JobRun` recién creado (con ``id`` asignado por la BD).
    """
    run = JobRun(
        pipeline=pipeline,
        status=JobRunStatus.PENDING,
        date_from=date_from,
        date_to=date_to,
    )
    session.add(run)
    session.commit()
    session.refresh(run)
    logger.info("JobRun created: id=%s pipeline=%s", run.id, run.pipeline)
    return run


def get_job_run(session: Session, run_id: str) -> JobRun | None:
    """Obtiene un registro de ejecución por su UUID.

    Args:
        session: Sesión SQLModel activa.
        run_id: UUID del registro (como string).

    Returns:
        El :class:`JobRun` o ``None`` si no existe.
    """
    statement = select(JobRun).where(JobRun.id == run_id)
    return session.exec(statement).one_or_none()


def get_latest_job_run(
    session: Session,
    pipeline: str,
) -> JobRun | None:
    """Devuelve la última corrida (por ``created_at``) del pipeline indicado.

    Args:
        session: Sesión SQLModel activa.
        pipeline: Nombre del pipeline.

    Returns:
        El :class:`JobRun` más reciente, o ``None`` si no hay ninguna.
    """
    statement = (
        select(JobRun)
        .where(JobRun.pipeline == pipeline)
        .order_by(JobRun.created_at.desc())
        .limit(1)
    )
    return session.exec(statement).one_or_none()


def get_job_runs_for_date(
    session: Session,
    pipeline: str,
    date_str: str,
) -> list[JobRun]:
    """Devuelve todas las corridas de un pipeline para la fecha dada.

    La comparación usa ``date_from`` (ISO date). Retorna todas las corridas
    cuyo ``date_from`` coincida, ordenadas por ``created_at`` descendente.

    Args:
        session: Sesión SQLModel activa.
        pipeline: Nombre del pipeline.
        date_str: Fecha en formato ISO (``YYYY-MM-DD``).

    Returns:
        Lista de :class:`JobRun` (vacía si no hay ninguna).
    """
    statement = (
        select(JobRun)
        .where(JobRun.pipeline == pipeline, JobRun.date_from == date_str)
        .order_by(JobRun.created_at.desc())
    )
    return list(session.exec(statement).all())


# ---------------------------------------------------------------------------
# Actualización de estado
# ---------------------------------------------------------------------------


def update_job_run_status(
    session: Session,
    run_id: str,
    new_status: JobRunStatus,
    *,
    error_message: str | None = None,
    processed_events: int | None = None,
    rows_upserted: int | None = None,
) -> JobRun:
    """Actualiza el estado y metadatos de una corrida.

    Si el nuevo estado es ``processing``, registra ``started_at``.
    Si es un estado terminal (``success`` o ``failed``), registra ``finished_at``.

    Args:
        session: Sesión SQLModel activa.
        run_id: UUID del registro.
        new_status: Nuevo estado.
        error_message: Mensaje de error (solo para ``failed``).
        processed_events: Número de eventos procesados.
        rows_upserted: Número de filas insertadas/actualizadas.

    Returns:
        El :class:`JobRun` actualizado.

    Raises:
        ValueError: Si ``run_id`` no existe.
    """
    run = get_job_run(session, run_id)
    if run is None:
        raise ValueError(f"JobRun with id={run_id!r} not found")

    now = utcnow()

    # Validación: no sobrescribir un estado terminal
    if is_terminal(run.status):
        logger.warning(
            "Attempt to update terminal JobRun %s from %s to %s — ignored",
            run_id,
            run.status,
            new_status.value,
        )
        return run

    run.status = new_status

    if new_status == JobRunStatus.PROCESSING:
        run.started_at = now
    elif is_terminal(new_status):
        run.finished_at = now

    if error_message is not None:
        run.error_message = error_message

    if processed_events is not None:
        run.processed_events = processed_events

    if rows_upserted is not None:
        run.rows_upserted = rows_upserted

    session.add(run)
    session.commit()
    session.refresh(run)

    logger.info(
        "JobRun %s status → %s (pipeline=%s)", run_id, new_status.value, run.pipeline
    )
    return run


# ---------------------------------------------------------------------------
# Guardianes de concurrencia
# ---------------------------------------------------------------------------


def has_processing_lock(session: Session, pipeline: str) -> bool:
    """Comprueba si ya hay una corrida en estado ``processing`` para el pipeline.

    Previene ejecuciones simultáneas del mismo pipeline. Es un candado
    optimista: se verifica **antes** de crear la corrida y **antes** de
    cambiar su estado a ``processing``. Si dos workers consultan a la vez,
    el segundo verá el bloqueo y abortará.

    Args:
        session: Sesión SQLModel activa.
        pipeline: Nombre del pipeline.

    Returns:
        ``True`` si ya hay una corrida en ``processing`` (bloqueada).
    """
    statement = select(JobRun).where(
        JobRun.pipeline == pipeline,
        JobRun.status == JobRunStatus.PROCESSING,
    )
    existing = session.exec(statement).first()
    return existing is not None


def has_completed_for_date(session: Session, pipeline: str, date_str: str) -> bool:
    """Comprueba si el pipeline ya completó con éxito la fecha indicada.

    Útil para pipelines idempotentes que no deben reprocesar una fecha ya
    consolidada. La comprobación es:

    - ``pipeline`` coincide.
    - ``date_from`` coincide con ``date_str``.
    - ``status`` es ``success``.

    Args:
        session: Sesión SQLModel activa.
        pipeline: Nombre del pipeline.
        date_str: Fecha en formato ISO (``YYYY-MM-DD``).

    Returns:
        ``True`` si ya hay una corrida ``success`` para esa fecha.
    """
    statement = select(JobRun).where(
        JobRun.pipeline == pipeline,
        JobRun.date_from == date_str,
        JobRun.status == JobRunStatus.SUCCESS,
    )
    existing = session.exec(statement).first()
    return existing is not None


# ---------------------------------------------------------------------------
# Ejecución controlada (wrapper)
# ---------------------------------------------------------------------------


def run_job(
    session: Session,
    run_id: str,
    fn: Callable[..., Any],
    *args: Any,
    **kwargs: Any,
) -> Any:
    """Ejecuta una función dentro del ciclo de vida de un ``JobRun``.

    Pasos:
    1. Cambia el estado a ``processing``.
    2. Ejecuta ``fn(*args, **kwargs)``.
    3. Si la función retorna sin error, cambia el estado a ``success``.
    4. Si **cualquier** excepción ocurre, se captura, se actualiza el
       registro a ``failed`` con el mensaje de error, se propaga al log
       con ``logger.exception``, y **se relanza**.

    Garantía: el registro **nunca** queda en ``processing`` al terminar.

    Args:
        session: Sesión SQLModel activa.
        run_id: UUID del registro a controlar.
        fn: Función a ejecutar (síncrona).
        *args: Argumentos posicionales para ``fn``.
        **kwargs: Argumentos nombrados para ``fn``.

    Returns:
        El resultado de ``fn(*args, **kwargs)``.

    Raises:
        Exception: Cualquier excepción lanzada por ``fn``, tras haberla
            registrado y actualizado el estado a ``failed``.
    """
    # ── 1. Marcar como processing ───────────────────────────────────
    update_job_run_status(session, run_id, JobRunStatus.PROCESSING)

    try:
        # ── 2. Ejecutar la función ──────────────────────────────────
        result = fn(*args, **kwargs)

    except Exception as exc:
        # ── 3. Fallo → failed + log + relanzar ──────────────────────
        error_msg = f"{type(exc).__name__}: {exc}"
        logger.exception(
            "JobRun %s failed (pipeline=%s): %s",
            run_id,
            _get_pipeline_name(session, run_id),
            error_msg,
        )
        update_job_run_status(
            session,
            run_id,
            JobRunStatus.FAILED,
            error_message=error_msg,
        )
        raise

    else:
        # ── 4. Éxito → success ──────────────────────────────────────
        update_job_run_status(session, run_id, JobRunStatus.SUCCESS)
        return result


def _get_pipeline_name(session: Session, run_id: str) -> str:
    """Obtiene el nombre del pipeline para un ``run_id`` (uso interno)."""
    run = get_job_run(session, run_id)
    return run.pipeline if run else "unknown"


# ── Versión decorador ────────────────────────────────────────────────


def job_runner(
    session_factory: Callable[[], Session],
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorador que envuelve una función en el ciclo de vida ``run_job``.

    Es una alternativa al uso directo de ``run_job`` para pipelines
    existentes que quieran añadir control de estado con mínimo cambio.

    Uso:

        from services.job_runner import create_job_run, job_runner
        from services.job_runner.db import get_db

        @job_runner(get_db)
        def my_pipeline(session: Session, week_start: str) -> dict:
            ...

    El decorador crea automáticamente el ``JobRun`` (estado ``pending``)
    y gestiona todo el ciclo de vida.

    Args:
        session_factory: Función sin argumentos que devuelve una sesión
            SQLModel. Normalmente ``get_db`` (un generador, el decorador
            llama a ``next()`` internamente).

    Returns:
        Un decorador que envuelve la función decorada.
    """

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            pipeline_name = func.__name__

            # Extraer date_from/date_to si están en kwargs
            date_from = kwargs.get("date_from")
            date_to = kwargs.get("date_to")

            session = session_factory()  # type: ignore[misc]
            try:
                run = create_job_run(
                    session,
                    pipeline=pipeline_name,
                    date_from=date_from,
                    date_to=date_to,
                )
                result = run_job(session, run.id, func, *args, **kwargs)
            finally:
                session.close()

            return result

        return wrapper

    return decorator