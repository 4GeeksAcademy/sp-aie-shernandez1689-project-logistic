"""Máquina de estados para ejecuciones de procesos en segundo plano.

Implementa el ciclo de vida:

    pending → processing → completed
                         ↘ failed

El estado ``processing`` actúa como **distributed lock** (job-level,
sin tabla o columna de lock aparte): si existe una fila con
status='processing' para el job (p. ej. ``nightly_export``), otra
instancia aborta silenciosamente. El lock se adquiere de forma atómica
en ``start_processing()`` mediante un UPDATE condicionado a que no
exista otra fila ``processing`` del mismo job.

La **idempotencia** se resuelve con la pareja (job_name, target_date):
si existe una fila ``completed`` para esa pareja exacta, el script no
vuelve a ejecutar el trabajo (solo job_name sin target_date no basta).

Transiciones:
    - ``create_run()`` → crea un registro en pending
    - ``start_processing()`` → pending → processing (lock job-level atómico)
    - ``complete_run()`` → processing → completed
    - ``fail_run()`` → processing → failed
"""

from __future__ import annotations

import logging
import uuid
from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import exists, update
from sqlmodel import Session, select

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
        La instancia ``JobRun`` creada (ya persistida), o el registro
        ``completed`` existente si la pareja (job_name, target_date) ya
        fue procesada (idempotencia). El llamador puede distinguir ambos
        casos mediante ``run.status``: ``completed`` indica que el trabajo
        ya se ejecutó y no debe relanzarse.
    """
    # Idempotencia: si ya existe un registro completed para
    # (job_name, target_date), devolver ese registro sin crear uno nuevo.
    # job_name sin target_date NO basta — la clave es la pareja exacta.
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

    # El id se asigna en cliente (uuid4) en vez de depender solo del
    # server_default gen_random_uuid(): esto hace la lógica portable a
    # SQLite en tests y garantiza que run.id esté poblado y estable tras
    # commit/refresh en cualquier backend.
    run = JobRun(
        id=str(uuid.uuid4()),
        job_name=job_name,
        target_date=target_date,
        status=JobRunStatus.PENDING.value,
    )
    session.add(run)
    session.commit()
    session.refresh(run)
    logger.info("Created job_run %s for %s target_date=%s", run.id, job_name, target_date)
    return run


def start_processing(session: Session, run_id: str, job_name: str | None = None,
                     target_date: date | None = None) -> bool:
    """Intenta adquirir el lock: pending → processing.

    El lock es **job-level** (estado ``processing`` del propio registro,
    sin tabla/columna aparte). Se adquiere de forma atómica: el UPDATE
    solo tiene éxito si la fila está ``pending`` **y** no existe otra
    fila ``processing`` para el mismo job. Si se pasa ``job_name`` (y
    opcionalmente ``target_date``), la condición de no-duplicado se aplica
    sobre ese job; de lo contrario solo sobre el propio ``run_id``.

    Args:
        session: Sesión SQLModel activa.
        run_id: ID del registro a procesar.
        job_name: Nombre del job (para el lock job-level).
        target_date: Fecha objetivo (opcional, afina el lock).

    Returns:
        ``True`` si se adquirió el lock (se actualizó a processing).
        ``False`` si el lock está tomado (otra instancia ya está
        procesando el job) o el registro ya no está en ``pending``.
    """
    now = datetime.now(timezone.utc)
    processing = JobRunStatus.PROCESSING.value
    pending = JobRunStatus.PENDING.value

    if job_name is None:
        # Compatibilidad con llamadores que solo pasan run_id:
        # UPDATE ... WHERE id = :id AND status = 'pending' (atómico por fila).
        stmt = (
            update(JobRun)
            .where(JobRun.id == run_id, JobRun.status == pending)
            .values(status=processing, started_at=now)
        )
    else:
        # Lock job-level atómico: tomamos la fila solo si sigue pending y
        # NO existe otra fila processing para el mismo job. Usamos el ORM
        # update() con subconsulta NOT EXISTS para que el binding del id
        # (UUID) sea consistente entre PostgreSQL y SQLite (tests).
        sub = select(JobRun.id).where(
            JobRun.job_name == job_name,
            JobRun.status == processing,
            JobRun.id != run_id,
        )
        if target_date is not None:
            sub = sub.where(JobRun.target_date == target_date)

        stmt = (
            update(JobRun)
            .where(
                JobRun.id == run_id,
                JobRun.status == pending,
                ~exists(sub),
            )
            .values(status=processing, started_at=now)
        )

    result = session.exec(stmt)
    session.commit()

    acquired = result.rowcount > 0
    if acquired:
        logger.info("Lock acquired for job_run %s", run_id)
    else:
        logger.warning(
            "Lock NOT acquired for job_run %s — another instance is already "
            "processing this job, or the run is no longer pending",
            run_id,
        )

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
    stmt = (
        update(JobRun)
        .where(
            JobRun.id == run_id,
            JobRun.status == JobRunStatus.PROCESSING.value,
        )
        .values(
            status=JobRunStatus.COMPLETED.value,
            finished_at=now,
        )
    )
    result = session.exec(stmt)
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
    stmt = (
        update(JobRun)
        .where(
            JobRun.id == run_id,
            JobRun.status == JobRunStatus.PROCESSING.value,
        )
        .values(
            status=JobRunStatus.FAILED.value,
            finished_at=now,
            error_message=error_message,
        )
    )
    result = session.exec(stmt)
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


def is_locked(session: Session, job_name: str, target_date: date | None = None) -> bool:
    """Verifica si el lock está tomado para un job.

    El lock es **job-level** (estado ``processing``): se comprueba si
    existe una fila con status='processing' para ``job_name`` — cualquier
    instancia en procesamiento cancela una nueva. Si se pasa
    ``target_date``, la comprobación se restringe a esa fecha exacta.

    Args:
        session: Sesión SQLModel activa.
        job_name: Nombre del job.
        target_date: Fecha objetivo (opcional).

    Returns:
        ``True`` si existe un registro en estado ``processing``.
    """
    query = select(JobRun).where(
        JobRun.job_name == job_name,
        JobRun.status == JobRunStatus.PROCESSING.value,
    )
    if target_date is not None:
        query = query.where(JobRun.target_date == target_date)

    existing = session.exec(query).first()
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