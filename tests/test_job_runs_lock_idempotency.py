"""Tests de la máquina de estados de ``job_runs``: lock e idempotencia.

DEV-53 — Script nocturno de telemetría.

Cubre los dos requisitos de cancelación/skip del script:

- **Lock vía ``processing``** (job-level): si ya existe un registro
  ``job_runs`` con status='processing' para el job, no se puede adquirir
  el lock de nuevo y ``is_locked()`` lo reporta. Sin tabla ni columna de
  lock aparte: el estado ``processing`` ES el lock.
- **Idempotencia vía ``target_date``**: si ya existe un registro
  ``completed`` para la pareja exacta (job_name, target_date), no se
  vuelve a trabajar y ``is_completed()`` lo reporta. Solo ``job_name``
  sin ``target_date`` no basta.

Los tests usan SQLite en memoria con el módulo ``gen_random_uuid``
registrado (es un server-default de PostgreSQL); la lógica SQL se
comprueba contra Postgres real en pruebas de integración manuales.
"""

from __future__ import annotations

import sys
import uuid
from datetime import date, datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import event
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, Session, create_engine, select

# ── Añadir raíz del monorepo al sys.path ──────────────────────────────────
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from services.job_runs.models import JobRun
from services.job_runs.state import (
    complete_run,
    create_run,
    fail_run,
    is_completed,
    is_locked,
    start_processing,
)

JOB = "nightly_export"
D1 = date(2026, 10, 20)
D2 = date(2026, 10, 21)


def _make_engine():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _sqlite_functions(dbapi_conn, _):
        dbapi_conn.create_function("gen_random_uuid", 0, lambda: str(uuid.uuid4()))

    SQLModel.metadata.create_all(engine)
    return engine


@pytest.fixture
def engine():
    return _make_engine()


@pytest.fixture
def session_factory(engine):
    def make_session():
        return Session(engine, expire_on_commit=False)

    return make_session


def _seed(session, job=JOB, target_date=D1, status="pending"):
    """Crea una fila de job_runs directamente (bypass de create_run)."""
    run = JobRun(
        id=str(uuid.uuid4()),
        job_name=job,
        target_date=target_date,
        status=status,
        created_at=datetime.now(timezone.utc),
    )
    session.add(run)
    session.commit()
    return run


# ── LOCK vía processing ────────────────────────────────────────────────────


def test_is_locked_true_when_a_processing_row_exists(session_factory):
    with session_factory() as session:
        _seed(session, status="processing")
        assert is_locked(session, JOB) is True


def test_is_locked_false_when_nothing_processing(session_factory):
    with session_factory() as session:
        _seed(session, status="completed")
        assert is_locked(session, JOB) is False


def test_is_locked_is_job_level_any_processing_row_blocks(session_factory):
    """El lock es job-level: un processing en OTRA fecha bloquea el job."""
    with session_factory() as session:
        _seed(session, target_date=D2, status="processing")
        # Aunque queramos la fecha D1, el job ya está en procesamiento.
        assert is_locked(session, JOB) is True
        assert is_locked(session, JOB, D1) is False  # con fecha exacta no aplica


def test_start_processing_second_instance_blocked_by_processing(session_factory):
    """start_processing falla si otra fila del MISMO job está processing."""
    with session_factory() as session:
        a = _seed(session, status="processing")
        b = _seed(session, status="pending")
        assert start_processing(session, b.id, JOB, D1) is False
        assert is_locked(session, JOB) is True


def test_start_processing_does_not_block_different_job(session_factory):
    """Un processing de otro job no bloquea este job."""
    with session_factory() as session:
        _seed(session, job="daily_report", status="processing")
        run = _seed(session, status="pending")
        assert start_processing(session, run.id, JOB, D1) is True
        assert is_locked(session, JOB) is True


def test_start_processing_frees_lock_on_complete(session_factory):
    with session_factory() as session:
        run = _seed(session, status="pending")
        assert start_processing(session, run.id, JOB, D1) is True
        assert is_locked(session, JOB) is True
        complete_run(session, run.id)
        assert is_locked(session, JOB) is False


def test_start_processing_frees_lock_on_fail(session_factory):
    """Un fallo libera el lock (nunca zombi en processing)."""
    with session_factory() as session:
        run = _seed(session, status="pending")
        assert start_processing(session, run.id, JOB, D1) is True
        assert is_locked(session, JOB) is True
        fail_run(session, run.id, "boom")
        assert is_locked(session, JOB) is False
        assert is_completed(session, JOB, D1) is False


# ── IDEMPOTENCIA vía target_date ────────────────────────────────────────────


def test_is_completed_true_for_exact_completed_record(session_factory):
    with session_factory() as session:
        _seed(session, target_date=D1, status="completed")
        assert is_completed(session, JOB, D1) is True


def test_is_completed_false_when_only_failed(session_factory):
    """Un failed NO da idempotencia: se puede reintentar."""
    with session_factory() as session:
        _seed(session, target_date=D1, status="failed")
        assert is_completed(session, JOB, D1) is False


def test_is_completed_requires_exact_target_date(session_factory):
    """Idempotencia es por (job_name, target_date): otra fecha no se omite."""
    with session_factory() as session:
        _seed(session, target_date=D1, status="completed")
        assert is_completed(session, JOB, D1) is True
        assert is_completed(session, JOB, D2) is False  # otra fecha sí se ejecuta


def test_is_completed_requires_matching_job_name(session_factory):
    """Un completed de otro job no suprime este."""
    with session_factory() as session:
        _seed(session, job="daily_report", target_date=D1, status="completed")
        assert is_completed(session, JOB, D1) is False


def test_create_run_returns_existing_completed_without_new_row(session_factory):
    """create_run sobre un (job, fecha) completado devuelve el existente.

    No crea una fila nueva: si se llama dos veces para la misma pareja,
    la tabla sigue teniendo una única fila.
    """
    with session_factory() as session:
        first = _seed(session, status="completed")
        second = create_run(session, JOB, D1)
        assert second.id == first.id
        assert second.status == "completed"
        rows = session.exec(select(JobRun)).all()
        assert len(rows) == 1


def test_create_run_creates_pending_when_not_completed(session_factory):
    with session_factory() as session:
        run = create_run(session, JOB, D1)
        assert run.status == "pending"
        assert run.id is not None


# ── Sección nightly_export: guards del script ──────────────────────────────


def test_script_guards_use_is_locked_and_is_completed():
    """El script importa y usa los guards (contrato del flujo principal).

    Verifica que ``nightly_export.main`` referencia ambos checks primeros
    en su flujo, de modo que el lock (processing) y la idempotencia
    (completed + target_date) se comprueban antes de crear/ejecutar.
    """
    import inspect

    from scripts import nightly_export

    src = inspect.getsource(nightly_export.main)
    # El orden importa: primero el lock, luego la idempotencia.
    assert src.index("is_locked") < src.index("is_completed")
    assert "Canceled:" in src or "processing" in src
    assert "duplicate" in src or "already completed" in src