"""Tests del módulo ``services/job_runner`` (control de estado, DEV-53).

Cubre las responsabilidades exigidas:

- **Crear / actualizar / consultar** ``job_runs``
  (``create_job_run``, ``update_job_run_status``, ``get_job_run``,
  ``get_latest_run``, ``get_runs_for_date``).
- **Lock vía ``processing``**: ``has_processing_lock`` + guard en
  ``run_job`` (sin tabla/columna de lock aparte).
- **Idempotencia vía ``target_date``**: ``has_completed_for_date`` + guard
  en ``run_job`` (solo job_name sin target_date no basta).
- **Garantías de fallo**: cualquier excepción de ``work`` se captura, el
  registro pasa a ``failed`` con el mensaje, el error se propaga al log y
  se re-lanza; nunca queda una fila en ``processing``.

Usa SQLite en memoria con ``gen_random_uuid`` registrado (server-default
de PostgreSQL); la lógica SQL se valida contra Postgres en integración.
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

from services.job_runner import (
    JobRunOutcome,
    create_job_run,
    get_job_run,
    get_latest_run,
    get_runs_for_date,
    has_completed_for_date,
    has_processing_lock,
    run_job,
    update_job_run_status,
)
from services.job_runs.models import JobRun, JobRunStatus

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
    """Crea una fila de job_runs directamente (bypass del runner)."""
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


# ── Crear / consultar ────────────────────────────────────────────────────────


def test_create_job_run_creates_pending(session_factory):
    with session_factory() as session:
        run = create_job_run(session, JOB, D1)
        assert run.status == "pending"
        assert run.id is not None
        assert run.job_name == JOB
        assert run.target_date == D1


def test_create_job_run_returns_existing_completed_idempotent(session_factory):
    with session_factory() as session:
        first = _seed(session, status="completed")
        second = create_job_run(session, JOB, D1)
        assert second.id == first.id
        assert second.status == "completed"
        rows = session.exec(select(JobRun)).all()
        assert len(rows) == 1  # no fila nueva


def test_get_job_run_returns_dict(session_factory):
    with session_factory() as session:
        run = _seed(session, status="completed")
        data = get_job_run(session, run.id)
        assert data is not None
        assert data["id"] == run.id
        assert data["status"] == "completed"
        assert get_job_run(session, "does-not-exist") is None


def test_get_latest_run_returns_most_recent(session_factory):
    with session_factory() as session:
        older = _seed(session, status="failed")
        newer = _seed(session, status="completed")
        latest = get_latest_run(session, JOB)
        assert latest is not None
        assert latest["id"] == newer.id
        assert latest["status"] == "completed"


def test_get_runs_for_date_returns_all(session_factory):
    with session_factory() as session:
        _seed(session, target_date=D1, status="failed")
        _seed(session, target_date=D1, status="completed")
        _seed(session, target_date=D2, status="completed")
        runs = get_runs_for_date(session, JOB, D1)
        assert len(runs) == 2


# ── Actualizar estado ────────────────────────────────────────────────────────


def test_update_job_run_status_to_processing(session_factory):
    with session_factory() as session:
        run = _seed(session, status="pending")
        ok = update_job_run_status(session, run.id, "processing")
        assert ok is True
        data = get_job_run(session, run.id)
        assert data["status"] == "processing"
        assert data["started_at"] is not None


def test_update_job_run_status_to_failed_saves_error(session_factory):
    with session_factory() as session:
        run = _seed(session, status="processing")
        ok = update_job_run_status(
            session, run.id, JobRunStatus.FAILED, error_message="boom"
        )
        assert ok is True
        data = get_job_run(session, run.id)
        assert data["status"] == "failed"
        assert data["error_message"] == "boom"
        assert data["finished_at"] is not None


def test_update_job_run_status_invalid_raises(session_factory):
    with session_factory() as session:
        run = _seed(session, status="pending")
        with pytest.raises(ValueError):
            update_job_run_status(session, run.id, "not-a-status")


def test_update_job_run_status_missing_row_returns_false(session_factory):
    with session_factory() as session:
        assert update_job_run_status(session, "no-such-id", "processing") is False


# ── Guards: lock e idempotencia ──────────────────────────────────────────────


def test_has_processing_lock_true_when_processing_exists(session_factory):
    with session_factory() as session:
        _seed(session, status="processing")
        assert has_processing_lock(session, JOB) is True


def test_has_processing_lock_false_when_none(session_factory):
    with session_factory() as session:
        _seed(session, status="completed")
        assert has_processing_lock(session, JOB) is False


def test_has_processing_lock_job_level(session_factory):
    """Un processing de OTRA fecha bloquea el job (lock job-level)."""
    with session_factory() as session:
        _seed(session, target_date=D2, status="processing")
        assert has_processing_lock(session, JOB) is True
        assert has_processing_lock(session, JOB, D1) is False


def test_has_completed_for_date_exact_pair(session_factory):
    with session_factory() as session:
        _seed(session, status="completed")
        assert has_completed_for_date(session, JOB, D1) is True


def test_has_completed_for_date_requires_exact_target_date(session_factory):
    with session_factory() as session:
        _seed(session, status="completed")  # D1
        assert has_completed_for_date(session, JOB, D1) is True
        assert has_completed_for_date(session, JOB, D2) is False


def test_has_completed_for_date_ignores_failed(session_factory):
    with session_factory() as session:
        _seed(session, status="failed")
        assert has_completed_for_date(session, JOB, D1) is False


def test_has_completed_for_date_requires_matching_job(session_factory):
    with session_factory() as session:
        _seed(session, job="daily_report", status="completed")
        assert has_completed_for_date(session, JOB, D1) is False


# ── run_job: ejecución orquestada ────────────────────────────────────────────


def _noop(session):
    pass


def test_run_job_executes_and_completes(session_factory):
    with session_factory() as session:
        executed = {"work": False}

        def work(s):
            executed["work"] = True

        outcome = run_job(session, JOB, D1, work)
        assert outcome.outcome == "executed"
        assert executed["work"] is True
        assert has_completed_for_date(session, JOB, D1) is True
        assert has_processing_lock(session, JOB) is False


def test_run_job_canceled_when_lock_held(session_factory):
    """Guard 1: si existe un processing, aborta sin ejecutar trabajo."""
    with session_factory() as session:
        _seed(session, status="processing")
        executed = {"work": False}

        def work(s):
            executed["work"] = True

        outcome = run_job(session, JOB, D1, work)
        assert outcome.outcome == "canceled_locked"
        assert executed["work"] is False
        rows = session.exec(select(JobRun)).all()
        # No crea un registro nuevo: solo queda la fila processing original.
        assert len(rows) == 1


def test_run_job_skipped_when_duplicate(session_factory):
    """Guard 2: completed para la pareja (job, fecha) → omitir."""
    with session_factory() as session:
        original = _seed(session, status="completed")
        executed = {"work": False}

        def work(s):
            executed["work"] = True

        outcome = run_job(session, JOB, D1, work)
        assert outcome.outcome == "skipped_duplicate"
        assert outcome.run_id == original.id  # traza el registro completed
        assert executed["work"] is False
        rows = session.exec(select(JobRun)).all()
        assert len(rows) == 1
        assert rows[0].id == original.id  # no fila nueva, no relanza


def test_run_job_skipped_does_not_cancel_other_date(session_factory):
    """completed de OTRA fecha no suprime: la pareja exacta es la clave."""
    with session_factory() as session:
        _seed(session, status="completed")  # D1
        executed = {"work": False}

        def work(s):
            executed["work"] = True

        outcome = run_job(session, JOB, D2, work)
        assert outcome.outcome == "executed"
        assert executed["work"] is True


def test_run_job_locked_when_atomic_race(session_factory, monkeypatch):
    """Re-chequeo atómico: otra instancia tomó el lock entre guards y UPDATE.

    Simulamos la carrera obligando a ``start_processing`` a fallar (como si
    otra instancia hubiera avanzado a processing entre el Guard 1 y el
    UPDATE) → outcome ``locked``, sin ejecutar trabajo y sin zombi.
    """
    import services.job_runner as job_runner

    with session_factory() as session:
        executed = {"work": False}

        def work(s):
            executed["work"] = True

        def fake_start_processing(s, run_id, job_name=None, target_date=None):
            return False

        monkeypatch.setattr(job_runner._state, "start_processing", fake_start_processing)

        outcome = run_job(session, JOB, D1, work)
        assert outcome.outcome == "locked"
        assert executed["work"] is False
        # El registro pending creado no debe quedarse en processing.
        assert has_processing_lock(session, JOB) is False


def test_run_job_marked_failed_and_no_processing_left_on_error(session_factory):
    """Requirement clave: excepción → failed, nunca queda processing."""
    with session_factory() as session:

        def work(s):
            raise RuntimeError("kaboom")

        with pytest.raises(RuntimeError, match="kaboom"):
            run_job(session, JOB, D1, work)

        rows = session.exec(select(JobRun)).all()
        assert len(rows) == 1
        assert rows[0].status == JobRunStatus.FAILED.value
        assert rows[0].error_message == "RuntimeError: kaboom"
        # Ningún registro puede quedar en processing tras una ejecución fallida.
        assert has_processing_lock(session, JOB) is False
        assert has_completed_for_date(session, JOB, D1) is False


def test_run_job_logs_error_on_exception(session_factory, caplog):
    """La excepción se propaga al log (logger.exception) y se re-lanza."""
    import logging

    with session_factory() as session:
        def work(s):
            raise ValueError("explota")

        with caplog.at_level(logging.ERROR, logger="services.job_runner"):
            with pytest.raises(ValueError, match="explota"):
                run_job(session, JOB, D1, work)

        # El error debe haber quedado registrado en el log.
        combined = "\n".join(rec.getMessage() for rec in caplog.records)
        assert "explota" in combined


def test_run_job_passes_session_to_work(session_factory):
    """work recibe la sesión activa (p. ej. para ejecutar consultas)."""
    with session_factory() as session:
        seen = {}

        def work(s):
            seen["same_session"] = s is session

        run_job(session, JOB, D1, work)
        assert seen["same_session"] is True


def test_run_job_outcome_dataclass_fields():
    o = JobRunOutcome(outcome="executed", job_name=JOB, target_date=D1, run_id="abc")
    assert o.outcome == "executed"
    assert o.run_id == "abc"
    assert o.job_name == JOB
    assert o.target_date == D1