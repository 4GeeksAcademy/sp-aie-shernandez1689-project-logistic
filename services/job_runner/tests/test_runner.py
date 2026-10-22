"""Tests del módulo job_runner — runner, guardianes y ciclo de vida.

Ejercita cada función del módulo con sesiones SQLite en memoria
(patrón idéntico al de ``test_database.py`` en services/user-api/tests/).

No requiere conexión a Supabase.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import ANY, patch

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

# Añadir la raíz del monorepo al sys.path
REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from services.job_runner.models import (
    JobRun,
    JobRunStatus,
    TERMINAL_STATUSES,
    is_terminal,
    utcnow,
)
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


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def engine():
    """Crea un engine SQLite en memoria con las tablas creadas."""
    e = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(e)
    return e


@pytest.fixture
def session(engine):
    """Crea una sesión limpia por test."""
    with Session(engine) as s:
        yield s


def _create_run(
    session: Session,
    pipeline: str = "test_pipeline",
    status: JobRunStatus = JobRunStatus.PENDING,
    date_from: str | None = None,
) -> JobRun:
    """Helper para insertar un JobRun directamente."""
    run = JobRun(
        pipeline=pipeline,
        status=status,
        date_from=date_from,
    )
    session.add(run)
    session.commit()
    session.refresh(run)
    return run


# ---------------------------------------------------------------------------
# Tests: create_job_run
# ---------------------------------------------------------------------------


class TestCreateJobRun:
    def test_creates_with_defaults(self, session: Session):
        """Crea un registro pending sin fechas."""
        run = create_job_run(session, pipeline="my_pipeline")
        assert run.id is not None
        assert run.pipeline == "my_pipeline"
        assert run.status == JobRunStatus.PENDING
        assert run.started_at is None
        assert run.finished_at is None
        assert run.date_from is None
        assert run.date_to is None
        assert run.processed_events == 0
        assert run.rows_upserted == 0
        assert run.error_message is None
        assert run.created_at is not None

    def test_creates_with_dates(self, session: Session):
        """Crea un registro pending con ventana de fechas."""
        run = create_job_run(
            session,
            pipeline="weekly_pipeline",
            date_from="2026-10-12",
            date_to="2026-10-19",
        )
        assert run.pipeline == "weekly_pipeline"
        assert run.date_from == "2026-10-12"
        assert run.date_to == "2026-10-19"

    def test_creates_multiple_runs(self, session: Session):
        """Puede haber múltiples corridas para el mismo pipeline."""
        r1 = create_job_run(session, pipeline="p1")
        r2 = create_job_run(session, pipeline="p1")
        assert r1.id != r2.id
        assert r1.pipeline == r2.pipeline == "p1"


# ---------------------------------------------------------------------------
# Tests: get_job_run
# ---------------------------------------------------------------------------


class TestGetJobRun:
    def test_finds_existing(self, session: Session):
        """Busca por UUID correctamente."""
        created = _create_run(session)
        fetched = get_job_run(session, created.id)
        assert fetched is not None
        assert fetched.id == created.id
        assert fetched.pipeline == created.pipeline

    def test_returns_none_for_missing(self, session: Session):
        """UUID inexistente → None."""
        fetched = get_job_run(session, "00000000-0000-0000-0000-000000000000")
        assert fetched is None

    def test_returns_none_for_empty_db(self, session: Session):
        """Base vacía → None."""
        fetched = get_job_run(session, "00000000-0000-0000-0000-000000000001")
        assert fetched is None


# ---------------------------------------------------------------------------
# Tests: get_latest_job_run
# ---------------------------------------------------------------------------


class TestGetLatestJobRun:
    def test_returns_most_recent(self, session: Session):
        """Devuelve la más reciente por created_at."""
        _create_run(session, pipeline="p1")
        _create_run(session, pipeline="p1")
        latest = _create_run(session, pipeline="p1")
        fetched = get_latest_job_run(session, "p1")
        assert fetched is not None
        assert fetched.id == latest.id

    def test_returns_none_when_no_runs(self, session: Session):
        """Sin corridas → None."""
        fetched = get_latest_job_run(session, "nonexistent")
        assert fetched is None

    def test_filters_by_pipeline(self, session: Session):
        """Solo devuelve la última del pipeline indicado."""
        _create_run(session, pipeline="p1")
        latest_p2 = _create_run(session, pipeline="p2")
        fetched = get_latest_job_run(session, "p2")
        assert fetched is not None
        assert fetched.id == latest_p2.id

        fetched_p1 = get_latest_job_run(session, "p1")
        assert fetched_p1 is not None
        assert fetched_p1.pipeline == "p1"
        assert fetched_p1.id != latest_p2.id


# ---------------------------------------------------------------------------
# Tests: get_job_runs_for_date
# ---------------------------------------------------------------------------


class TestGetJobRunsForDate:
    def test_returns_matching_runs(self, session: Session):
        """Devuelve corridas que coinciden con date_from."""
        _create_run(session, pipeline="p1", date_from="2026-10-12")
        _create_run(session, pipeline="p1", date_from="2026-10-12")
        _create_run(session, pipeline="p1", date_from="2026-10-13")

        runs = get_job_runs_for_date(session, "p1", "2026-10-12")
        assert len(runs) == 2
        assert all(r.date_from == "2026-10-12" for r in runs)

    def test_returns_empty_for_no_match(self, session: Session):
        """Sin corridas para esa fecha → lista vacía."""
        _create_run(session, pipeline="p1", date_from="2026-10-12")
        runs = get_job_runs_for_date(session, "p1", "2026-10-13")
        assert runs == []

    def test_filters_by_pipeline(self, session: Session):
        """Solo corridas del pipeline indicado."""
        _create_run(session, pipeline="p1", date_from="2026-10-12")
        _create_run(session, pipeline="p2", date_from="2026-10-12")

        runs = get_job_runs_for_date(session, "p1", "2026-10-12")
        assert len(runs) == 1
        assert runs[0].pipeline == "p1"


# ---------------------------------------------------------------------------
# Tests: update_job_run_status
# ---------------------------------------------------------------------------


class TestUpdateJobRunStatus:
    def test_updates_to_processing(self, session: Session):
        """Cambio a processing registra started_at."""
        run = _create_run(session)
        updated = update_job_run_status(session, run.id, JobRunStatus.PROCESSING)
        assert updated.status == JobRunStatus.PROCESSING
        assert updated.started_at is not None
        assert updated.finished_at is None
        assert updated.error_message is None

    def test_updates_to_success(self, session: Session):
        """Cambio a success registra finished_at."""
        run = _create_run(session, status=JobRunStatus.PROCESSING)
        updated = update_job_run_status(session, run.id, JobRunStatus.SUCCESS)
        assert updated.status == JobRunStatus.SUCCESS
        assert updated.finished_at is not None

    def test_updates_to_failed_with_message(self, session: Session):
        """Cambio a failed con mensaje de error."""
        run = _create_run(session, status=JobRunStatus.PROCESSING)
        updated = update_job_run_status(
            session,
            run.id,
            JobRunStatus.FAILED,
            error_message="TimeoutError: connection reset",
        )
        assert updated.status == JobRunStatus.FAILED
        assert updated.finished_at is not None
        assert updated.error_message == "TimeoutError: connection reset"

    def test_updates_processed_events(self, session: Session):
        """Actualiza processed_events y rows_upserted."""
        run = _create_run(session, status=JobRunStatus.PROCESSING)
        updated = update_job_run_status(
            session,
            run.id,
            JobRunStatus.SUCCESS,
            processed_events=100,
            rows_upserted=50,
        )
        assert updated.processed_events == 100
        assert updated.rows_upserted == 50

    def test_raises_on_nonexistent_run(self, session: Session):
        """UUID inexistente → ValueError."""
        with pytest.raises(ValueError, match="not found"):
            update_job_run_status(
                session,
                "00000000-0000-0000-0000-000000000000",
                JobRunStatus.SUCCESS,
            )

    def test_ignores_update_on_terminal_status(self, session: Session):
        """No modifica un registro que ya está en estado terminal."""
        run = _create_run(session, status=JobRunStatus.SUCCESS)
        updated = update_job_run_status(session, run.id, JobRunStatus.PROCESSING)
        # Debe seguir en success (el log advierte pero no cambia)
        assert updated.status == JobRunStatus.SUCCESS


# ---------------------------------------------------------------------------
# Tests: has_processing_lock
# ---------------------------------------------------------------------------


class TestHasProcessingLock:
    def test_returns_true_when_processing_exists(self, session: Session):
        """Hay una corrida en processing → True."""
        _create_run(session, pipeline="p1", status=JobRunStatus.PROCESSING)
        assert has_processing_lock(session, "p1") is True

    def test_returns_false_when_no_processing(self, session: Session):
        """Sin corridas en processing → False."""
        _create_run(session, pipeline="p1", status=JobRunStatus.SUCCESS)
        assert has_processing_lock(session, "p1") is False

    def test_returns_false_on_empty_db(self, session: Session):
        """Base vacía → False."""
        assert has_processing_lock(session, "p1") is False

    def test_filters_by_pipeline(self, session: Session):
        """Solo comprueba el pipeline indicado."""
        _create_run(session, pipeline="p1", status=JobRunStatus.PROCESSING)
        assert has_processing_lock(session, "p2") is False

    def test_multiple_processing_runs_detected(self, session: Session):
        """Múltiples corridas processing → True."""
        _create_run(session, pipeline="p1", status=JobRunStatus.PROCESSING)
        _create_run(session, pipeline="p1", status=JobRunStatus.PROCESSING)
        assert has_processing_lock(session, "p1") is True


# ---------------------------------------------------------------------------
# Tests: has_completed_for_date
# ---------------------------------------------------------------------------


class TestHasCompletedForDate:
    def test_returns_true_when_success_exists(self, session: Session):
        """Hay un success para la fecha → True."""
        _create_run(
            session,
            pipeline="p1",
            status=JobRunStatus.SUCCESS,
            date_from="2026-10-12",
        )
        assert has_completed_for_date(session, "p1", "2026-10-12") is True

    def test_returns_false_when_only_failed(self, session: Session):
        """Solo hay failed para la fecha → False."""
        _create_run(
            session,
            pipeline="p1",
            status=JobRunStatus.FAILED,
            date_from="2026-10-12",
        )
        assert has_completed_for_date(session, "p1", "2026-10-12") is False

    def test_returns_false_for_different_date(self, session: Session):
        """Success en otra fecha → False para la fecha no procesada."""
        _create_run(
            session,
            pipeline="p1",
            status=JobRunStatus.SUCCESS,
            date_from="2026-10-12",
        )
        assert has_completed_for_date(session, "p1", "2026-10-13") is False

    def test_filters_by_pipeline(self, session: Session):
        """Success en otro pipeline → False."""
        _create_run(
            session,
            pipeline="p1",
            status=JobRunStatus.SUCCESS,
            date_from="2026-10-12",
        )
        assert has_completed_for_date(session, "p2", "2026-10-12") is False

    def test_returns_false_on_empty_db(self, session: Session):
        """Base vacía → False."""
        assert has_completed_for_date(session, "p1", "2026-10-12") is False


# ---------------------------------------------------------------------------
# Tests: run_job (ciclo de vida completo)
# ---------------------------------------------------------------------------


class TestRunJob:
    def test_successful_execution(self, session: Session):
        """Ejecución exitosa → estado success."""
        run = _create_run(session)

        def my_action() -> str:
            return "ok"

        result = run_job(session, run.id, my_action)
        assert result == "ok"

        # Verificar estado final
        updated = get_job_run(session, run.id)
        assert updated is not None
        assert updated.status == JobRunStatus.SUCCESS
        assert updated.started_at is not None
        assert updated.finished_at is not None
        assert updated.error_message is None

    def test_failed_execution_sets_failed_and_relanza(self, session: Session):
        """Excepción → estado failed + relanzamiento."""
        run = _create_run(session)

        def failing_action() -> None:
            raise ValueError("Algo salió mal")

        with pytest.raises(ValueError, match="Algo salió mal"):
            run_job(session, run.id, failing_action)

        # Verificar que el registro se actualizó a failed
        updated = get_job_run(session, run.id)
        assert updated is not None
        assert updated.status == JobRunStatus.FAILED
        assert updated.started_at is not None
        assert updated.finished_at is not None
        assert updated.error_message is not None
        assert "ValueError" in updated.error_message
        assert "Algo salió mal" in updated.error_message

    def test_nunca_queda_en_processing_tras_fallo(self, session: Session):
        """Garantía: ningún registro queda en processing tras fallo."""
        run = _create_run(session)

        with pytest.raises(RuntimeError):
            run_job(session, run.id, lambda: (_ for _ in ()).throw(RuntimeError("fail")))

        updated = get_job_run(session, run.id)
        assert updated is not None
        assert updated.status == JobRunStatus.FAILED

    def test_failed_execution_with_args(self, session: Session):
        """Función con argumentos también captura excepciones."""
        run = _create_run(session)

        def process_week(week: str) -> int:
            if week == "bad":
                raise KeyError(f"Unknown week: {week}")
            return 42

        with pytest.raises(KeyError, match="Unknown week"):
            run_job(session, run.id, process_week, "bad")

        updated = get_job_run(session, run.id)
        assert updated is not None
        assert updated.status == JobRunStatus.FAILED
        assert "KeyError" in updated.error_message
        assert "Unknown week" in updated.error_message

    def test_successful_execution_with_args(self, session: Session):
        """Función con argumentos se ejecuta correctamente."""
        run = _create_run(session)

        def process_week(week: str) -> int:
            return 42

        result = run_job(session, run.id, process_week, "2026-10-12")
        assert result == 42

        updated = get_job_run(session, run.id)
        assert updated is not None
        assert updated.status == JobRunStatus.SUCCESS

    def test_logs_exception_on_failure(self, session: Session, caplog):
        """El logger registra la excepción con logger.exception."""
        run = _create_run(session)

        with pytest.raises(ValueError):
            run_job(session, run.id, lambda: (_ for _ in ()).throw(ValueError("boom")))

        # Verificar que hay un log de nivel ERROR con el mensaje
        assert any(
            record.levelname == "ERROR" and "boom" in record.message
            for record in caplog.records
        )


# ---------------------------------------------------------------------------
# Tests del modelo
# ---------------------------------------------------------------------------


class TestJobRunModel:
    def test_is_terminal(self):
        """is_terminal detecta estados terminales."""
        assert is_terminal(JobRunStatus.SUCCESS) is True
        assert is_terminal(JobRunStatus.FAILED) is True
        assert is_terminal(JobRunStatus.PENDING) is False
        assert is_terminal(JobRunStatus.PROCESSING) is False

    def test_terminal_statuses_set(self):
        """TERMINAL_STATUSES contiene solo success y failed."""
        assert JobRunStatus.SUCCESS in TERMINAL_STATUSES
        assert JobRunStatus.FAILED in TERMINAL_STATUSES
        assert JobRunStatus.PENDING not in TERMINAL_STATUSES
        assert JobRunStatus.PROCESSING not in TERMINAL_STATUSES

    def test_utcnow_returns_aware_datetime(self):
        """utcnow devuelve un datetime timezone-aware."""
        now = utcnow()
        assert now.tzinfo is not None
        assert now.tzinfo.utcoffset(now) == timezone.utc.utcoffset(now)