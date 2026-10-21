"""Máquina de estados para ejecuciones de procesos en segundo plano.

Implementa el ciclo de vida:

    pending → processing → completed
                         ↘ failed

El estado ``processing`` actúa como **distributed lock**: si existe una
fila con (job_name, target_date, status='processing'), otra instancia
aborta silenciosamente.

Transiciones:
    - ``create_run()`` → crea un registro en pending
    - ``start_processing()`` → pending → processing (lock)
    - ``complete_run()`` → processing → completed
    - ``fail_run()`` → processing → failed
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import Any

from sqlmodel import Session, select, text

from services.job_runs.database import get_engine
from services.job_runs.models import JobRun, JobRunStatus

logger = logging.getLogger(__name__)


def create_run(session: Session, job_name: str, target_date: date) -> JobRun:
    """Crea un registro en estado ``pending``.

    Args:
        session: Sesión SQLModel activa.
        job_name: Nombre del job.
        target_date: Fecha objetivo (UTC).

    Returns:
        La instancia ``JobRun`` creada (ya persistida).

    Note:
        Si ya existe un registro ``completed`` para (job_name, target_date),
        la función retorna el registro existente sin crear uno nuevo
        (idempotencia — verifica antes de crear).
    """
    # Verificar si ya hay un registro completed (idempotencia)
    existing = session.exec(
        select(JobRun).where(
            JobRun.job_name == job_name,
            JobRun.target_date == target_date,
            JobRun.status == JobRunStatus.COMPLETED.value,
        )
    ).first()

    if existing is not None:
        logger.info(
            "Job %s for target_date %s already completed (run %s) — skipping",
            job_name,
            target_date,
            existing.id,
        )
        return existing

    run = JobRun(
        job_name=job_name,
        target_date=target_date,
        status=JobRunStatus.PENDING.value,
    )
    session.add(run)
    session.commit()
    session.refresh(run)
    logger.info("Created job_run %s for %s target_date=%s", run.id, job_name, target_date)
    return run


def start_processing(session: Session, run_id: str) -> bool:
    """Intenta adquirir el lock: pending → processing.

    Args:
        session: Sesión SQLModel activa.
        run_id: ID del registro a procesar.

    Returns:
        ``True`` si se adquirió el lock (se actualizó a processing).
        ``False`` si el lock está tomado (otra instancia ya está procesando).
    """
    now = datetime.now(timezone.utc)

    # UPDATE ... SET status = 'processing' WHERE id = :id AND status = 'pending'
    # Atomic: si otra instancia ya lo tomó, la fila afectada es 0.
    stmt = text(
        "UPDATE job_runs "
        "SET status = :processing, started_at = :now "
        "WHERE id = :id AND status = :pending"
    )
    result = session.exec(
        stmt,
        params={
            "processing": JobRunStatus.PROCESSING.value,
            "pending": JobRunStatus.PENDING.value,
            "now": now,
            "id": run_id,
        },
    )
    session.commit()

    acquired = result.rowcount > 0
    if acquired:
        logger.info("Lock acquired for job_run %s", run_id)
    else:
        logger.warning("Lock NOT acquired for job_run %s — already taken or invalid", run_id)

    return acquired


def complete_run(session: Session, run_id: str) -> bool:
    """Transición: processing → completed.

    Args:
        session: Sesión SQLModel activa.
        run_id: ID del registro a completar.

    Returns:
        ``True`` si se actualizó correctamente.
    """
    now = datetime.now(timezone.utc)
    stmt = text(
        "UPDATE job_runs "
        "SET status = :completed, finished_at = :now "
        "WHERE id = :id AND status = :processing"
    )
    result = session.exec(
        stmt,
        params={
            "completed": JobRunStatus.COMPLETED.value,
            "processing": JobRunStatus.PROCESSING.value,
            "now": now,
            "id": run_id,
        },
    )
    session.commit()

    success = result.rowcount > 0
    if success:
        logger.info("Job_run %s completed", run_id)
    else:
        logger.warning("Failed to complete job_run %s — not in processing state", run_id)

    return success


def fail_run(session: Session, run_id: str, error_message: str) -> bool:
    """Transición: processing → failed.

    Args:
        session: Sesión SQLModel activa.
        run_id: ID del registro a marcar como fallido.
        error_message: Mensaje de la excepción.

    Returns:
        ``True`` si se actualizó correctamente.
    """
    now = datetime.now(timezone.utc)
    stmt = text(
        "UPDATE job_runs "
        "SET status = :failed, finished_at = :now, error_message = :error "
        "WHERE id = :id AND status = :processing"
    )
    result = session.exec(
        stmt,
        params={
            "failed": JobRunStatus.FAILED.value,
            "processing": JobRunStatus.PROCESSING.value,
            "now": now,
            "id": run_id,
            "error": error_message,
        },
    )
    session.commit()

    success = result.rowcount > 0
    if success:
        logger.error("Job_run %s failed: %s", run_id, error_message)
    else:
        logger.warning("Failed to mark job_run %s as failed — not in processing state", run_id)

    return success


def get_run(session: Session, run_id: str) -> dict[str, Any] | None:
    """Obtiene un registro por su ID.

    Args:
        session: Sesión SQLModel activa.
        run_id: ID del registro.

    Returns:
        Diccionario con los datos del registro, o ``None`` si no existe.
    """
    run = session.get(JobRun, run_id)
    if run is None:
        return None

    return {
        "id": str(run.id),
        "job_name": run.job_name,
        "target_date": str(run.target_date),
        "status": run.status,
        "started_at": str(run.started_at) if run.started_at else None,
        "finished_at": str(run.finished_at) if run.finished_at else None,
        "error_message": run.error_message,
        "created_at": str(run.created_at),
    }


def is_locked(session: Session, job_name: str, target_date: date) -> bool:
    """Verifica si el lock está tomado para (job_name, target_date).

    Args:
        session: Sesión SQLModel activa.
        job_name: Nombre del job.
        target_date: Fecha objetivo.

    Returns:
        ``True`` si existe un registro en estado ``processing``.
    """
    existing = session.exec(
        select(JobRun).where(
            JobRun.job_name == job_name,
            JobRun.target_date == target_date,
            JobRun.status == JobRunStatus.PROCESSING.value,
        )
    ).first()

    return existing is not None


def is_completed(session: Session, job_name: str, target_date: date) -> bool:
    """Verifica si ya se completó el job para la fecha dada (idempotencia).

    Args:
        session: Sesión SQLModel activa.
        job_name: Nombre del job.
        target_date: Fecha objetivo.

    Returns:
        ``True`` si existe un registro en estado ``completed``.
    """
    existing = session.exec(
        select(JobRun).where(
            JobRun.job_name == job_name,
            JobRun.target_date == target_date,
            JobRun.status == JobRunStatus.COMPLETED.value,
        )
    ).first()

    return existing is not None


def get_latest_run(session: Session, job_name: str) -> dict[str, Any] | None:
    """Obtiene la ejecución más reciente de un job.

    Args:
        session: Sesión SQLModel activa.
        job_name: Nombre del job.

    Returns:
        Diccionario con los datos del run más reciente, o ``None``.
    """
    run = session.exec(
        select(JobRun)
        .where(JobRun.job_name == job_name)
        .order_by(JobRun.created_at.desc())
        .limit(1)
    ).first()

    if run is None:
        return None

    return {
        "id": str(run.id),
        "job_name": run.job_name,
        "target_date": str(run.target_date),
        "status": run.status,
        "started_at": str(run.started_at) if run.started_at else None,
        "finished_at": str(run.finished_at) if run.finished_at else None,
        "error_message": run.error_message,
        "created_at": str(run.created_at),
    }


def get_runs_for_date(
    session: Session,
    job_name: str,
    target_date: str,
) -> list[dict[str, Any]]:
    """Obtiene todos los intentos de ejecución para (job_name, target_date).

    Args:
        session: Sesión SQLModel activa.
        job_name: Nombre del job.
        target_date: Fecha objetivo.

    Returns:
        Lista de diccionarios con los datos de los runs.
    """
    runs = session.exec(
        select(JobRun)
        .where(
            JobRun.job_name == job_name,
            JobRun.target_date == target_date,
        )
        .order_by(JobRun.created_at.asc())
    ).all()

    return [
        {
            "id": str(run.id),
            "job_name": run.job_name,
            "target_date": str(run.target_date),
            "status": run.status,
            "started_at": str(run.started_at) if run.started_at else None,
            "finished_at": str(run.finished_at) if run.finished_at else None,
            "error_message": run.error_message,
            "created_at": str(run.created_at),
        }
        for run in runs
    ]