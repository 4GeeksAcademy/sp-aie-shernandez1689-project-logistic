"""Orquestador de ejecuciones de procesos en segundo plano (DEV-53).

Módulo de control de estado en ``services/`` que envuelve la máquina de
estados de ``job_runs`` (``services/job_runs/state.py``) y garantiza las
propiedades exigidas:

- Funciones para **crear** (``create_job_run``), **actualizar**
  (``update_job_run_status``) y **consultar** (``get_job_run``,
  ``get_latest_run``, ``get_runs_for_date``) registros ``job_runs``.
- ``has_processing_lock``: el distributed lock es el estado ``processing``
  mismo — no una tabla, columna o flag aparte.
- ``has_completed_for_date``: idempotencia por la pareja exacta
  (job_name, target_date); solo job_name sin target_date no basta.
- ``run_job``: ejecución orquestada con dos garantías fuertes:
    1. **Cualquier excepción no controlada se captura**, el registro se
       actualiza a ``failed`` con el mensaje de error y el error se
       propaga al log (``logger.exception``) — y se re-lanza al llamador.
    2. **Ningún registro queda en estado ``processing`` tras una
       ejecución fallida** (success → ``completed``, excepción → ``failed``).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import update
from sqlmodel import Session

from services.job_runs import state as _state
from services.job_runs.models import JobRun, JobRunStatus

logger = logging.getLogger(__name__)

__all__ = [
    "JobRun",
    "JobRunStatus",
    "JobRunOutcome",
    "create_job_run",
    "update_job_run_status",
    "get_job_run",
    "get_latest_run",
    "get_runs_for_date",
    "has_processing_lock",
    "has_completed_for_date",
    "run_job",
]


# ── Resultado de run_job ─────────────────────────────────────────


@dataclass
class JobRunOutcome:
    """Resultado de una ejecución orquestada con ``run_job``.

    Attributes:
        outcome: Uno de:
            - ``"executed"``: el trabajo terminó bien (→ completed).
            - ``"canceled_locked"``: otra instancia ya procesaba el job
              (Guard 1, previo a crear el registro).
            - ``"skipped_duplicate"``: ya existe un ``completed`` para la
              pareja (job_name, target_date) (Guard 2, idempotencia).
            - ``"locked"``: no se pudo adquirir el lock en el re-chequeo
              atómico (otra instancia avanzó entre los guards y el UPDATE).
        run_id: ID del registro involucrado (última fila creada o usada).
        job_name: Nombre del job.
        target_date: Fecha objetivo (UTC).
    """

    outcome: str
    job_name: str
    target_date: date
    run_id: str | None = None


# ── Crear / actualizar / consultar job_runs ──────────────────────


def create_job_run(session: Session, job_name: str, target_date: date) -> JobRun:
    """Crea un registro en estado ``pending`` (o devuelve el ``completed``).

    Idempotente: si ya existe un registro ``completed`` para la pareja
    exacta (job_name, target_date), devuelve ese registro sin crear una
    fila nueva. El llamador distingue ambos casos por ``run.status``.

    Args:
        session: Sesión SQLModel activa.
        job_name: Nombre del job.
        target_date: Fecha objetivo (UTC).

    Returns:
        ``JobRun`` en estado ``pending`` (creado) o ``completed`` (existente).
    """
    return _state.create_run(session, job_name, target_date)


def update_job_run_status(
    session: Session,
    run_id: str,
    new_status: str | JobRunStatus,
    *,
    error_message: str | None = None,
) -> bool:
    """Actualiza el estado de un registro ``job_runs``.

    Cualquier actualización de estado pasa por aquí (consulta simple,
    sin condiciones de lock adicionales; el lock se adquiere con
    ``_state.start_processing`` / ``run_job``).

    Mantiene las marcas de tiempo consistentes:
        - ``processing`` → fija ``started_at``.
        - ``completed`` / ``failed`` → fija ``finished_at``.
        - ``failed`` → guarda ``error_message``.

    Args:
        session: Sesión SQLModel activa.
        run_id: ID del registro a actualizar.
        new_status: Nuevo estado (string o miembro de ``JobRunStatus``).
        error_message: Mensaje de error (solo para ``failed``).

    Returns:
        ``True`` si la fila fue actualizada.

    Raises:
        ValueError: Si ``new_status`` no es un estado válido.
    """
    if isinstance(new_status, JobRunStatus):
        status = new_status.value
    else:
        status = str(new_status)

    valid = {s.value for s in JobRunStatus}
    if status not in valid:
        raise ValueError(f"Invalid status: {status!r} (expected one of {valid})")

    now = datetime.now(timezone.utc)
    values: dict[str, Any] = {"status": status}
    if status == JobRunStatus.PROCESSING.value:
        values["started_at"] = now
    elif status in (JobRunStatus.COMPLETED.value, JobRunStatus.FAILED.value):
        values["finished_at"] = now
    if status == JobRunStatus.FAILED.value and error_message is not None:
        values["error_message"] = error_message

    stmt = update(JobRun).where(JobRun.id == run_id).values(**values)
    result = session.exec(stmt)
    session.commit()
    return result.rowcount > 0


def get_job_run(session: Session, run_id: str) -> dict[str, Any] | None:
    """Consulta un registro por su ID (dict) o ``None`` si no existe."""
    return _state.get_run(session, run_id)


def get_latest_run(session: Session, job_name: str) -> dict[str, Any] | None:
    """Consulta la ejecución más reciente de un job (dict) o ``None``."""
    return _state.get_latest_run(session, job_name)


def get_runs_for_date(
    session: Session,
    job_name: str,
    target_date: str | date,
) -> list[dict[str, Any]]:
    """Consulta todos los intentos de ejecución para (job_name, target_date)."""
    target = target_date.isoformat() if isinstance(target_date, date) else str(target_date)
    return _state.get_runs_for_date(session, job_name, target)


# ── Guards del script: lock e idempotencia ───────────────────────


def has_processing_lock(
    session: Session,
    job_name: str,
    target_date: date | None = None,
) -> bool:
    """Verifica si el lock está tomado para un job.

    El distributed lock es **el estado ``processing`` mismo** (job-level,
    sin tabla/columna aparte): ``True`` si existe una fila ``processing``
    para ``job_name``. Con ``target_date`` se restringe a esa fecha exacta.
    """
    return _state.is_locked(session, job_name, target_date)


def has_completed_for_date(
    session: Session,
    job_name: str,
    target_date: date,
) -> bool:
    """Verifica idempotencia: existe ``completed`` para (job_name, target_date).

    Solo job_name sin target_date no basta — la clave es la pareja exacta.
    """
    return _state.is_completed(session, job_name, target_date)


# ── Ejecución orquestada ─────────────────────────────────────────


def run_job(
    session: Session,
    job_name: str,
    target_date: date,
    work: Callable[[Session], Any],
) -> JobRunOutcome:
    """Ejecuta un trabajo bajo la máquina de estados de ``job_runs``.

    Flujo:
        1. **Guard 1 (lock)** — ``has_processing_lock``: si otra instancia
           ya tiene una fila ``processing`` para el job, aborta
           silenciosamente y devuelve ``canceled_locked``.
        2. **Guard 2 (idempotencia)** — ``has_completed_for_date``: si ya
           existe un ``completed`` para la pareja (job_name, target_date),
           omite el trabajo y devuelve ``skipped_duplicate``.
        3. Crea el registro en ``pending`` y adquiere el lock de forma
           atómica (``_state.start_processing``). Si el lock lo tomó otra
           instancia en el ínterin, devuelve ``locked``.
        4. Ejecuta ``work(session)``.
           - Éxito → ``complete_run`` → devuelve ``executed``.
           - **Cualquier excepción → ``fail_run`` con el mensaje de error,
             se registra con ``logger.exception`` y se re-lanza.** Nunca se
             deja el registro en ``processing``.

    Args:
        session: Sesión SQLModel activa.
        job_name: Nombre del job.
        target_date: Fecha objetivo (UTC).
        work: Función que ejecuta el trabajo real; recibe la sesión.

    Returns:
        ``JobRunOutcome`` con el resultado.

    Raises:
        La excepción lanzada por ``work`` (tras marcarla como ``failed``).
    """
    # ── Guard 1: LOCK vía processing (job-level) ──────────────────
    if has_processing_lock(session, job_name):
        logger.info(
            "Canceled: job '%s' is already processing "
            "(status='processing' exists in job_runs). Aborting silently.",
            job_name,
        )
        return JobRunOutcome(
            outcome="canceled_locked",
            job_name=job_name,
            target_date=target_date,
        )

    # ── Guard 2: IDEMPOTENCIA vía target_date ────────────────────
    if has_completed_for_date(session, job_name, target_date):
        # Localizar el ID del registro completed existente para trazabilidad.
        existing_id: str | None = None
        for run in _state.get_runs_for_date(
            session, job_name, target_date.isoformat()
        ):
            if run.get("status") == JobRunStatus.COMPLETED.value:
                existing_id = run.get("id")
                break
        logger.info(
            "Skipped (duplicate): job '%s' already completed for "
            "target_date=%s. No work will run.",
            job_name,
            target_date,
        )
        return JobRunOutcome(
            outcome="skipped_duplicate",
            run_id=existing_id,
            job_name=job_name,
            target_date=target_date,
        )

    # ── Crear registro en pending ────────────────────────────────
    run = create_job_run(session, job_name, target_date)
    run_id = run.id

    # ── Adquirir lock (atómico, job-level) ───────────────────────
    # Re-chequeo: otra instancia pudo crear/avanzar su propio registro
    # entre el Guard 1 y este UPDATE.
    if not _state.start_processing(session, run_id, job_name, target_date):
        logger.info(
            "Lock not acquired for '%s' target_date=%s — another instance "
            "is already processing. Aborting silently.",
            job_name,
            target_date,
        )
        return JobRunOutcome(
            outcome="locked",
            run_id=run_id,
            job_name=job_name,
            target_date=target_date,
        )

    # ── Ejecutar el trabajo con garantía de estado ────────────────
    try:
        work(session)
    except BaseException as exc:  # noqa: BLE001 — garantía de no dejar zombis
        # Ningún registro puede quedar en 'processing' tras un fallo.
        try:
            _state.fail_run(session, run_id, f"{type(exc).__name__}: {exc}")
        except Exception as mark_err:  # noqa: BLE001
            logger.error("Could not mark run %s as failed: %s", run_id, mark_err)
        # Propagar el error al log (traza completa) y re-lanzar al llamador.
        logger.exception(
            "Job '%s' failed for target_date=%s (run %s): %s",
            job_name,
            target_date,
            run_id,
            exc,
        )
        raise
    else:
        _state.complete_run(session, run_id)
        logger.info(
            "Job '%s' completed for target_date=%s (run %s).",
            job_name,
            target_date,
            run_id,
        )
        return JobRunOutcome(
            outcome="executed",
            run_id=run_id,
            job_name=job_name,
            target_date=target_date,
        )